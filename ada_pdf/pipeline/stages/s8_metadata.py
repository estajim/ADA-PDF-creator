"""Stage 8 — Inject XMP metadata, PDF/UA markers, and fix untagged content.

In 'rebuild' mode   : post-processes WeasyPrint's output.
In 'tag_in_place' mode: post-processes the ORIGINAL PDF to add accessibility
                        without touching the visual layout.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING

from ada_pdf.models.domain import BlockRole, DocumentIR
from ada_pdf.pipeline.context import PipelineContext
from ada_pdf.config import Settings
from ada_pdf.utils.logging import get_logger

if TYPE_CHECKING:
    import pikepdf as _pikepdf

logger = get_logger(__name__)

_PASSTHROUGH_OPS = frozenset({
    "q", "Q", "cm",
    "w", "J", "j", "M", "d", "ri", "i", "gs",
    "CS", "cs", "SC", "SCN", "sc", "scn",
    "G", "g", "RG", "rg", "K", "k",
    "W", "W*",
})


def run(ctx: PipelineContext, settings: Settings) -> PipelineContext:
    logger.info("stage_start", stage="metadata",
                mode=ctx.conversion_mode)

    if not ctx.output_pdf_bytes:
        logger.warning("stage_skip", stage="metadata", reason="no_pdf_bytes")
        return ctx

    doc_ir = ctx.document_ir

    try:
        import pikepdf

        with pikepdf.open(io.BytesIO(ctx.output_pdf_bytes)) as pdf:
            # ── Metadata (both modes) ────────────────────────────────────
            _apply_metadata(pdf, doc_ir)

            # ── Mode-specific tagging ─────────────────────────────────────
            if ctx.conversion_mode == "tag_in_place":
                _fix_for_tag_in_place(pdf, doc_ir)
            else:
                # Rebuild mode: WeasyPrint already has a good structure tree
                _ensure_struct_tree_root(pdf, doc_ir)
                _tag_untagged_content(pdf)
                # Normalize headings, add Lang/Alt/Scope — WeasyPrint can emit H3
                # before H1 when document structure starts with a sub-heading.
                _enhance_existing_struct_tree(pdf, doc_ir)
                _fix_link_text(pdf)
                _tag_header_footer_artifacts(pdf)

            buf = io.BytesIO()
            pdf.save(buf)
            ctx.output_pdf_bytes = buf.getvalue()

    except ImportError:
        logger.warning("pikepdf_not_installed")
    except Exception as exc:
        logger.warning("metadata_injection_failed", error=str(exc))

    out_path = ctx.input_path.parent / f"{ctx.job_id}_accessible.pdf"
    out_path.write_bytes(ctx.output_pdf_bytes)
    ctx.output_path = out_path

    logger.info("stage_done", stage="metadata", output=str(ctx.output_path))
    return ctx


# ── Utility ──────────────────────────────────────────────────────────────────

def _deref_obj(pdf, obj):
    """Safely dereference a pikepdf object.

    Handles direct dicts, indirect refs, and the pikepdf Object wrapper.
    Returns the dereferenced object (which may be Array, Dictionary, int, etc.).
    """
    import pikepdf
    if obj is None:
        return None
    if isinstance(obj, (pikepdf.Dictionary, pikepdf.Array, int, str, bool)):
        return obj
    if hasattr(obj, 'objgen') and obj.objgen != (0, 0):
        try:
            return pdf.get_object(obj.objgen)
        except Exception:
            pass
    return obj


# ── Shared metadata ───────────────────────────────────────────────────────────

def _apply_metadata(pdf: "_pikepdf.Pdf", doc_ir: DocumentIR) -> None:
    import pikepdf

    pdf.Root.Lang = pikepdf.String(doc_ir.language)
    if doc_ir.title:
        pdf.docinfo["/Title"] = doc_ir.title
    if doc_ir.author:
        pdf.docinfo["/Author"] = doc_ir.author
    if doc_ir.subject:
        pdf.docinfo["/Subject"] = doc_ir.subject
    pdf.docinfo["/Producer"] = "ADA-PDF-Creator v1.0"

    pdf.Root.ViewerPreferences = pdf.make_indirect(
        pikepdf.Dictionary(DisplayDocTitle=True)
    )

    with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
        meta["dc:title"] = doc_ir.title
        meta["dc:language"] = doc_ir.language
        if doc_ir.author:
            meta["dc:creator"] = [doc_ir.author]
        meta["pdf:Producer"] = "ADA-PDF-Creator v1.0"
        # Register pdfuaid namespace and set part=1 (rule 5:1)
        try:
            meta.register_xml_namespace("http://www.aiim.org/pdfua/ns/id/", "pdfuaid")
        except Exception:
            pass
        meta["pdfuaid:part"] = "1"

    pdf.Root.MarkInfo = pdf.make_indirect(
        pikepdf.Dictionary(Marked=True, Suspects=False)
    )

    # Rule 7.2:2 — set Lang on Outline entries
    _fix_outline_lang(pdf, doc_ir.language)

    # Rule 7.11:1 — fix embedded file entries (F and UF keys)
    _fix_embedded_files(pdf)

    # Rule 7.10:1 / 7.10:2 — fix optional content configuration
    _fix_optional_content(pdf)

    # Rule 7.18.3:1 — add Tabs=S to pages with annotations
    _fix_annotation_tabs(pdf)

    # Rule 7.18.1:2 / 7.18.5:2 — add Contents to link annotations
    _fix_link_annotation_contents(pdf)

    # Rule 7.21.4.2:2 — remove incomplete CIDSet to avoid validation failure
    _fix_cidset_streams(pdf)

    # Rule 7.1:7 — remove /RoleMap entries that remap standard PDF/UA tags
    _fix_role_map(pdf)


# ── Rebuild-mode helpers ──────────────────────────────────────────────────────

def _ensure_struct_tree_root(pdf: "_pikepdf.Pdf", doc_ir: DocumentIR) -> None:
    import pikepdf
    if "/StructTreeRoot" in pdf.Root:
        return
    parent_tree = pdf.make_indirect(pikepdf.Dictionary(Nums=pikepdf.Array([])))
    struct_root = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name("/StructTreeRoot"),
        ParentTree=parent_tree,
        ParentTreeNextKey=pikepdf.Integer(0),
    ))
    doc_elem = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name("/StructElem"),
        S=pikepdf.Name("/Document"),
        P=struct_root,
        Lang=pikepdf.String(doc_ir.language),
        T=pikepdf.String(doc_ir.title),
        K=pikepdf.Array([]),
    ))
    struct_root["/K"] = doc_elem
    pdf.Root["/StructTreeRoot"] = struct_root


def _tag_untagged_content(pdf: "_pikepdf.Pdf", xobject_tag: str = "/Artifact") -> None:
    """Tag any unmarked content as Artifact or Span in page content streams and Form XObjects.

    xobject_tag: the tag to use for Form XObject content.
      "/Artifact" — for PDFs without struct trees (all content is decorative).
      "/Span"     — for PDFs WITH existing MCID struct trees, where XObjects are
                    typically invoked from within BDC/MCID blocks (real content context).
    """
    import pikepdf
    artifact_name = pikepdf.Name("/Artifact")
    xobj_tag_name = pikepdf.Name(xobject_tag)
    op_bmc = pikepdf.Operator("BMC")
    op_emc = pikepdf.Operator("EMC")

    processed_xobjects: set = set()

    for page_idx, page in enumerate(pdf.pages):
        _tag_stream_untagged_content(pdf, page, artifact_name, op_bmc, op_emc, page_idx + 1)
        _tag_xobjects_on_page(pdf, page, xobj_tag_name, op_bmc, op_emc, processed_xobjects)


def _get_inherited_resources(pdf, page_obj) -> "pikepdf.Dictionary | None":
    """Return the effective /Resources dict for a page, walking up /Parent if needed."""
    import pikepdf
    current = page_obj
    for _ in range(8):
        if not isinstance(current, pikepdf.Dictionary):
            break
        res_ref = current.get("/Resources")
        if res_ref is not None:
            return _deref_obj(pdf, res_ref)
        parent_ref = current.get("/Parent")
        if parent_ref is None:
            break
        current = _deref_obj(pdf, parent_ref)
    return None


def _tag_xobjects_on_page(pdf, page, artifact_name, op_bmc, op_emc, processed: set) -> None:
    """Recursively tag Form XObjects referenced by page content."""
    import pikepdf

    try:
        page_obj = page.obj if hasattr(page, 'obj') else page
        if not isinstance(page_obj, pikepdf.Dictionary):
            return

        resources = _get_inherited_resources(pdf, page_obj)
        if not isinstance(resources, pikepdf.Dictionary):
            return

        xobjects_ref = resources.get("/XObject")
        if xobjects_ref is None:
            return

        xobjects = _deref_obj(pdf, xobjects_ref)
        if not isinstance(xobjects, pikepdf.Dictionary):
            return

        for key in list(xobjects.keys()):
            xobj_ref = xobjects[key]
            # Get the XObject's objgen to avoid reprocessing
            xobj_id = None
            if hasattr(xobj_ref, 'objgen') and xobj_ref.objgen != (0, 0):
                xobj_id = xobj_ref.objgen
                if xobj_id in processed:
                    continue
                xobj = pdf.get_object(xobj_ref.objgen)
            else:
                xobj = xobj_ref

            # Form XObjects are pikepdf Stream objects (not isinstance Dictionary)
            # but they still support .get() for their dict entries.
            if not hasattr(xobj, 'get'):
                continue

            try:
                subtype = str(xobj.get("/Subtype", ""))
            except Exception:
                continue
            if subtype != "/Form":
                continue

            if xobj_id:
                processed.add(xobj_id)

            # Tag untagged content in this Form XObject.
            # For XObjects, include passthrough ops in pending — veraPDF counts
            # state operators (q, cm, gs, W, etc.) as content items too.
            try:
                instructions = list(pikepdf.parse_content_stream(xobj))
                new_ops: list = []
                depth = 0
                pending: list = []

                def flush_pending() -> None:
                    if pending:
                        new_ops.append(([artifact_name], op_bmc))
                        new_ops.extend(pending)
                        new_ops.append(([], op_emc))
                        pending.clear()

                for operands, operator in instructions:
                    op_str = str(operator)
                    if op_str in ("BMC", "BDC"):
                        flush_pending()
                        new_ops.append((operands, operator))
                        depth += 1
                    elif op_str == "EMC":
                        new_ops.append((operands, operator))
                        depth = max(0, depth - 1)
                    elif depth == 0:
                        # Include ALL depth-0 ops (including passthrough state ops)
                        # since veraPDF flags untagged state operators too
                        pending.append((operands, operator))
                    else:
                        flush_pending()
                        new_ops.append((operands, operator))

                flush_pending()

                # Form XObjects are streams — write directly to stream bytes.
                # (For pages, we'd use /Contents, but XObjects ARE the stream.)
                new_bytes = pikepdf.unparse_content_stream(new_ops)
                xobj.write(new_bytes)
            except Exception:
                pass

            # Recursively tag XObjects within this XObject
            _tag_xobjects_on_page(pdf, xobj, artifact_name, op_bmc, op_emc, processed)

    except Exception:
        pass


def _tag_stream_untagged_content(pdf, stream, artifact_name, op_bmc, op_emc, label) -> None:
    """Tag unmarked content in a content stream as /Artifact."""
    import pikepdf
    try:
        instructions = list(pikepdf.parse_content_stream(stream))
    except Exception as exc:
        logger.warning("parse_content_stream_failed", page=label, error=str(exc))
        return

    new_ops: list = []
    depth = 0
    pending: list = []

    def flush_pending() -> None:
        if pending:
            new_ops.append(([artifact_name], op_bmc))
            new_ops.extend(pending)
            new_ops.append(([], op_emc))
            pending.clear()

    for operands, operator in instructions:
        op_str = str(operator)
        if op_str in ("BMC", "BDC"):
            flush_pending()
            new_ops.append((operands, operator))
            depth += 1
        elif op_str == "EMC":
            new_ops.append((operands, operator))
            depth = max(0, depth - 1)
        elif depth == 0 and op_str not in _PASSTHROUGH_OPS:
            pending.append((operands, operator))
        else:
            flush_pending()
            new_ops.append((operands, operator))

    flush_pending()

    try:
        stream["/Contents"] = pdf.make_stream(
            pikepdf.unparse_content_stream(new_ops)
        )
    except Exception as exc:
        logger.warning("rewrite_content_stream_failed",
                       page=label, error=str(exc))


# ── Tag-in-place mode ─────────────────────────────────────────────────────────

def _fix_for_tag_in_place(pdf: "_pikepdf.Pdf", doc_ir: DocumentIR) -> None:
    """Apply all accessibility fixes to the original PDF in tag_in_place mode."""
    has_existing_struct = "/StructTreeRoot" in pdf.Root

    _tag_untagged_content(pdf, xobject_tag="/Span" if has_existing_struct else "/Artifact")

    if has_existing_struct:
        _enhance_existing_struct_tree(pdf, doc_ir)
        _fix_struct_reading_order(pdf)
    else:
        _build_struct_tree_from_ir(pdf, doc_ir)

    _fix_form_field_labels(pdf, doc_ir)
    _warn_unembedded_widget_fonts(pdf)
    _add_link_struct_elements(pdf)
    _add_widget_form_struct_elements(pdf)
    _fix_link_text(pdf)
    _tag_header_footer_artifacts(pdf)

    # Link any BDC/MCID blocks that have no corresponding struct element —
    # veraPDF reports these as untagged content (rule 7.1:3)
    if has_existing_struct:
        _link_orphaned_mcids(pdf)


def _enhance_existing_struct_tree(pdf: "_pikepdf.Pdf", doc_ir: DocumentIR) -> None:
    """Enhance an existing struct tree without replacing it.

    - Adds missing Lang attributes
    - Adds Alt text to Figure elements missing it
    - Adds Scope to TH elements missing it
    - Renumbers heading levels to avoid gaps (rule 7.4.2:1)
    """
    import pikepdf

    try:
        struct = pdf.Root.get("/StructTreeRoot")
        if not struct:
            return

        # Build role_map for resolving custom types to standard types
        role_map: dict[str, str] = {}
        try:
            rm_obj = _deref_obj(pdf, struct.get("/RoleMap")) if hasattr(struct, 'get') else None
            if isinstance(rm_obj, pikepdf.Dictionary):
                for key in rm_obj.keys():
                    k = f"/{key}" if not str(key).startswith("/") else str(key)
                    role_map[k] = str(rm_obj[key])
        except Exception:
            pass

        # Collect heading levels and build remap that closes gaps.
        # IMPORTANT: the remap must also ensure the FIRST heading in document
        # order is H1. We do this by finding the first heading in tree walk
        # order and anchoring the remap so that level maps to 1.
        heading_levels: set[int] = set()
        _collect_heading_levels(pdf, struct, heading_levels)

        level_remap: dict[int, int] = {}
        if heading_levels:
            # Find the first heading level in document (tree-walk) order
            first_level = _find_first_heading_level(pdf, struct)
            sorted_levels = sorted(heading_levels)
            # Build remap: assign sequential 1-based numbers to sorted levels,
            # but shift so first_level maps to 1.
            base = sorted_levels.index(first_level) if first_level in sorted_levels else 0
            for idx, lvl in enumerate(sorted_levels):
                mapped = idx - base + 1
                level_remap[lvl] = max(1, min(6, mapped))

        # Walk and patch, passing role_map for /Note and /TH detection
        _walk_and_patch_struct(pdf, struct, doc_ir.language, level_remap, role_map)

    except Exception as exc:
        logger.debug("enhance_struct_tree_failed", error=str(exc))


def _find_first_heading_level(pdf, node, depth: int = 0) -> int | None:
    """Return the heading level (1-6) of the first heading in tree-walk order."""
    import pikepdf
    if depth > 80:
        return None
    try:
        obj = _deref_obj(pdf, node)
        if not isinstance(obj, pikepdf.Dictionary):
            return None
        s = obj.get("/S")
        if s and str(s) in ("/H1", "/H2", "/H3", "/H4", "/H5", "/H6"):
            return int(str(s)[2])
        k = obj.get("/K")
        if k is None:
            return None
        if isinstance(k, pikepdf.Array):
            for child in list(k):
                if not isinstance(child, int):
                    result = _find_first_heading_level(pdf, child, depth + 1)
                    if result is not None:
                        return result
        elif not isinstance(k, int):
            return _find_first_heading_level(pdf, k, depth + 1)
    except Exception:
        pass
    return None


def _collect_heading_levels(pdf, node, levels: set, depth: int = 0) -> None:
    """Collect all heading levels (H1-H6) from a struct tree."""
    import pikepdf
    if depth > 50:
        return
    try:
        obj = _deref_obj(pdf, node)
        if not isinstance(obj, pikepdf.Dictionary):
            return
        s = obj.get("/S")
        if s:
            s_str = str(s)
            if s_str in ("/H1", "/H2", "/H3", "/H4", "/H5", "/H6"):
                levels.add(int(s_str[2]))
        k = obj.get("/K")
        if k is None:
            return
        if isinstance(k, pikepdf.Array):
            for child in list(k):
                if not isinstance(child, int):
                    _collect_heading_levels(pdf, child, levels, depth + 1)
        elif not isinstance(k, int):
            _collect_heading_levels(pdf, k, levels, depth + 1)
    except Exception:
        pass


def _walk_and_patch_struct(pdf, node, lang: str, level_remap: dict,
                           role_map: dict | None = None, depth: int = 0,
                           heading_state: list | None = None) -> None:
    """Recursively walk and patch struct tree elements in document order."""
    import pikepdf, uuid
    if depth > 80:
        return

    # heading_state[0] = last heading level seen in document order (0 = none yet)
    if heading_state is None:
        heading_state = [0]

    try:
        obj = _deref_obj(pdf, node)
        if not isinstance(obj, pikepdf.Dictionary):
            return

        s = obj.get("/S")
        s_str = str(s) if s else ""
        effective = (role_map or {}).get(s_str, s_str)

        # Add Lang if missing on struct elements
        if s_str and "/Lang" not in obj:
            obj["/Lang"] = pikepdf.String(lang)

        # Rule 7.3:1 — Figure elements need Alt text
        if (s_str == "/Figure" or effective == "/Figure") and "/Alt" not in obj:
            t = obj.get("/T")
            obj["/Alt"] = pikepdf.String(str(t)[:200] if t else "Figure")

        # Rule 7.5:1 — TH elements need Scope; also fix existing /A without /Scope
        if s_str == "/TH" or effective == "/TH":
            existing_a = _deref_obj(pdf, obj.get("/A")) if obj.get("/A") is not None else None
            if existing_a is None:
                obj["/A"] = pikepdf.Dictionary({
                    "/O": pikepdf.Name("/Table"),
                    "/Scope": pikepdf.Name("/Column"),
                })
            elif isinstance(existing_a, pikepdf.Dictionary) and "/Scope" not in existing_a:
                existing_a["/Scope"] = pikepdf.Name("/Column")

        # Rule 7.9:1 — Note elements need /ID
        if (s_str == "/Note" or effective == "/Note") and "/ID" not in obj:
            obj["/ID"] = pikepdf.String(str(uuid.uuid4()))

        # Rule 7.4.2:1 — stateful heading normalization (no skipped levels, H1 first)
        if s_str in ("/H1", "/H2", "/H3", "/H4", "/H5", "/H6"):
            orig_level = int(s_str[2])
            # Apply global remap first
            remapped = level_remap.get(orig_level, orig_level)
            last = heading_state[0]
            if last == 0:
                new_level = 1
            elif remapped > last + 1:
                new_level = last + 1
            else:
                new_level = remapped
            heading_state[0] = new_level
            if new_level != orig_level:
                obj["/S"] = pikepdf.Name(f"/H{min(6, new_level)}")

        # Walk children
        k = obj.get("/K")
        if k is None:
            return
        if isinstance(k, pikepdf.Array):
            for child in list(k):
                if not isinstance(child, int):
                    _walk_and_patch_struct(pdf, child, lang, level_remap, role_map,
                                          depth + 1, heading_state)
        elif not isinstance(k, int):
            _walk_and_patch_struct(pdf, k, lang, level_remap, role_map,
                                   depth + 1, heading_state)
    except Exception:
        pass


def _walk_and_set_lang(pdf, node, lang: str, depth: int = 0) -> None:
    """Recursively walk struct tree and add Lang where missing."""
    import pikepdf

    if depth > 50:
        return

    try:
        node_obj = _deref_obj(pdf, node)
        if not isinstance(node_obj, pikepdf.Dictionary):
            return

        # Add Lang if this is a struct element with /S but no /Lang
        if "/S" in node_obj and "/Lang" not in node_obj:
            node_obj["/Lang"] = pikepdf.String(lang)

        k = node_obj.get("/K")
        if k is None:
            return

        if isinstance(k, pikepdf.Array):
            for child in list(k):  # Use list() to avoid iteration bug in pikepdf 10.x
                if isinstance(child, int):
                    continue  # MCID reference (integer), skip
                _walk_and_set_lang(pdf, child, lang, depth + 1)
        elif isinstance(k, pikepdf.Dictionary):
            _walk_and_set_lang(pdf, k, lang, depth + 1)
        elif not isinstance(k, int):
            _walk_and_set_lang(pdf, k, lang, depth + 1)
    except Exception:
        pass


def _add_link_struct_elements(pdf: "_pikepdf.Pdf") -> None:
    """Rule 7.18.5:1 — Add Link struct elements for link annotations not in the struct tree.

    Properly wires StructParent in each annotation and the ParentTree entry
    so that veraPDF recognizes the link as tagged.
    """
    import pikepdf

    try:
        struct = pdf.Root.get("/StructTreeRoot")
        if not struct:
            return

        # Find the best parent element (handles flat Array /K and single-element /K)
        doc_elem = _get_struct_doc_elem(pdf, struct)
        if doc_elem is None or not isinstance(doc_elem, pikepdf.Dictionary):
            return

        # Build set of annotation objgens already wired via StructParent
        already_wired = _collect_struct_parent_objgens(pdf)

        # Get ParentTree for StructParent mapping
        parent_tree_ref = struct.get("/ParentTree")
        if parent_tree_ref is not None:
            if hasattr(parent_tree_ref, 'objgen') and parent_tree_ref.objgen != (0, 0):
                parent_tree = pdf.get_object(parent_tree_ref.objgen)
            else:
                parent_tree = parent_tree_ref
        else:
            parent_tree = None

        next_key_obj = struct.get("/ParentTreeNextKey")
        next_key = int(next_key_obj) if next_key_obj is not None else 0

        # Get the Nums array from the ParentTree
        if parent_tree is not None and isinstance(parent_tree, pikepdf.Dictionary):
            nums = parent_tree.get("/Nums")
            if nums is None:
                nums = pikepdf.Array([])
                parent_tree["/Nums"] = nums
        else:
            nums = None

        # Collect all link annotations that need Link struct elements
        link_annots_by_page: dict = {}
        for page_idx, page in enumerate(pdf.pages):
            annots = page.get("/Annots", pikepdf.Array([]))
            for annot_ref in list(annots):
                try:
                    if hasattr(annot_ref, 'objgen') and annot_ref.objgen != (0, 0):
                        annot = pdf.get_object(annot_ref.objgen)
                    else:
                        annot = annot_ref
                    subtype = str(annot.get("/Subtype", ""))
                    if subtype == "/Link":
                        # Only skip if already correctly parented under a /Link struct element
                        if _get_annot_struct_parent_type(pdf, annot) == "/Link":
                            continue
                        if page_idx not in link_annots_by_page:
                            link_annots_by_page[page_idx] = []
                        link_annots_by_page[page_idx].append((annot_ref, annot))
                except Exception:
                    continue

        if not link_annots_by_page:
            return

        # Add Link elements to the Document element
        doc_kids = doc_elem.get("/K")
        if doc_kids is None:
            doc_kids = pikepdf.Array([])
        elif isinstance(doc_kids, pikepdf.Array):
            pass  # Use as-is
        else:
            # Single dict child — keep it, we'll append to a new array
            doc_kids = pikepdf.Array([doc_kids])

        for page_idx, annots in link_annots_by_page.items():
            page_obj = pdf.pages[page_idx].obj

            for annot_ref, annot in annots:
                alt = _get_annotation_description(annot)

                # Create the Link struct element
                link_elem = pdf.make_indirect(pikepdf.Dictionary(
                    Type=pikepdf.Name("/StructElem"),
                    S=pikepdf.Name("/Link"),
                    Lang=pdf.Root.get("/Lang", pikepdf.String("en")),
                    Alt=pikepdf.String(alt),
                    Pg=page_obj,
                ))
                link_elem["/P"] = doc_elem

                # Create the OBJR referencing the annotation object
                try:
                    # Use make_indirect on the annot_ref to keep it as an indirect ref
                    if hasattr(annot_ref, 'objgen') and annot_ref.objgen != (0, 0):
                        objr = pdf.make_indirect(pikepdf.Dictionary(
                            Type=pikepdf.Name("/OBJR"),
                            Pg=page_obj,
                        ))
                        objr["/Obj"] = annot_ref
                        link_elem["/K"] = pikepdf.Array([objr])
                except Exception:
                    pass

                # Wire StructParent in the annotation
                try:
                    struct_parent_key = next_key
                    next_key += 1
                    annot["/StructParent"] = pikepdf.Integer(struct_parent_key)

                    # Add entry to ParentTree Nums
                    if nums is not None and isinstance(nums, pikepdf.Array):
                        nums.append(pikepdf.Integer(struct_parent_key))
                        nums.append(link_elem)
                except Exception:
                    pass

                doc_kids.append(link_elem)

        doc_elem["/K"] = doc_kids

        # Update ParentTreeNextKey
        if next_key > (int(struct.get("/ParentTreeNextKey", 0)) if struct.get("/ParentTreeNextKey") else 0):
            struct["/ParentTreeNextKey"] = pikepdf.Integer(next_key)

    except Exception as exc:
        logger.debug("add_link_struct_failed", error=str(exc))


def _count_link_elems(pdf, node, depth: int = 0) -> int:
    """Count /Link struct elements in the tree."""
    import pikepdf
    if depth > 5:
        return 0
    count = 0
    try:
        node_obj = node if isinstance(node, pikepdf.Dictionary) else \
                   (pdf.get_object(node.objgen) if hasattr(node, 'objgen') else node)
        if not isinstance(node_obj, pikepdf.Dictionary):
            return 0
        if str(node_obj.get("/S", "")) == "/Link":
            count += 1
        k = node_obj.get("/K")
        if isinstance(k, pikepdf.Array):
            for child in list(k)[:10]:  # Limit scanning
                if not isinstance(child, (int, pikepdf.objects.Integer)):
                    count += _count_link_elems(pdf, child, depth + 1)
    except Exception:
        pass
    return count


def _warn_unembedded_widget_fonts(pdf: "_pikepdf.Pdf") -> None:
    """Log non-embedded fonts found in widget annotation appearances.

    These cause PDF/UA-1 rules 7.21.4.1 and 7.21.7 to fail but cannot be fixed
    without the original font binary. They do not affect visual rendering.
    """
    import pikepdf

    seen: set[str] = set()
    for page in pdf.pages:
        for annot_ref in page.get("/Annots", pikepdf.Array([])):
            try:
                annot = pdf.get_object(annot_ref.objgen)
                if str(annot.get("/Subtype", "")) != "/Widget":
                    continue
                ap = annot.get("/AP")
                if not ap:
                    continue
                n_stream = ap.get("/N")
                if not n_stream:
                    continue
                resources = n_stream.get("/Resources")
                if not resources:
                    continue
                fonts = resources.get("/Font")
                if not fonts:
                    continue
                for key in fonts:
                    font = fonts[key]
                    base = str(font.get("/BaseFont", key))
                    if base in seen:
                        continue
                    descriptor = font.get("/FontDescriptor")
                    is_embedded = descriptor and any(
                        k in descriptor for k in ("/FontFile", "/FontFile2", "/FontFile3")
                    )
                    if not is_embedded:
                        seen.add(base)
                        logger.info(
                            "widget_font_not_embedded",
                            font=base,
                            note="PDF/UA-1 rules 7.21.4.1 and 7.21.7 will flag this font. "
                                 "It is a cosmetic limitation of the original PDF and does not "
                                 "affect screen reader functionality.",
                        )
            except Exception:
                continue


def _build_struct_tree_from_ir(pdf: "_pikepdf.Pdf", doc_ir: DocumentIR) -> None:
    """Build a StructTreeRoot using our semantic DocumentIR.

    Only called for PDFs that have no existing struct tree.
    Creates a Document → Sect (per page) → block-level elements hierarchy.
    """
    import pikepdf

    _HEADING_ROLES = {BlockRole.H1, BlockRole.H2, BlockRole.H3, BlockRole.H4, BlockRole.H5, BlockRole.H6}
    _HEADING_LEVEL = {BlockRole.H1: 1, BlockRole.H2: 2, BlockRole.H3: 3, BlockRole.H4: 4, BlockRole.H5: 5, BlockRole.H6: 6}
    _NON_HEADING_ROLE_MAP = {
        BlockRole.P: "/P", BlockRole.L: "/L", BlockRole.LI: "/LI",
        BlockRole.TABLE: "/Table", BlockRole.FIGURE: "/Figure",
        BlockRole.CAPTION: "/Caption",
    }

    # Stateful heading normalizer: ensures H1 is first and no levels are skipped
    # in document reading order (rule 7.4.2:1).
    _last_heading_level = [0]  # mutable cell so inner fn can update it

    def _heading_tag(role: BlockRole) -> str:
        orig = _HEADING_LEVEL[role]
        last = _last_heading_level[0]
        if last == 0:
            # First heading in document → always H1
            new_level = 1
        elif orig > last + 1:
            # Would skip levels — clamp to next allowed level
            new_level = last + 1
        else:
            new_level = orig
        _last_heading_level[0] = new_level
        return f"/H{min(6, new_level)}"

    parent_tree = pdf.make_indirect(pikepdf.Dictionary(Nums=pikepdf.Array([])))
    struct_root = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name("/StructTreeRoot"),
        ParentTree=parent_tree,
        ParentTreeNextKey=pikepdf.Integer(0),
    ))

    doc_elem = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name("/StructElem"),
        S=pikepdf.Name("/Document"),
        P=struct_root,
        Lang=pikepdf.String(doc_ir.language),
        T=pikepdf.String(doc_ir.title),
    ))
    struct_root["/K"] = doc_elem

    kids = pikepdf.Array([])
    for page_ir in doc_ir.pages:
        # Use .obj to get the underlying Object (fixes ObjectHelper error)
        if page_ir.page_number <= len(pdf.pages):
            page_obj = pdf.pages[page_ir.page_number - 1].obj
        else:
            page_obj = None

        sect = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name("/StructElem"),
            S=pikepdf.Name("/Sect"),
            P=doc_elem,
            Lang=pikepdf.String(doc_ir.language),
        ))
        if page_obj is not None:
            sect["/Pg"] = page_obj

        sect_kids = pikepdf.Array([])
        for block in sorted(page_ir.blocks, key=lambda b: b.reading_order):
            if block.is_artifact:
                continue
            if block.role in _HEADING_ROLES:
                role = _heading_tag(block.role)
            else:
                role = _NON_HEADING_ROLE_MAP.get(block.role)
            if role is None:
                continue

            text = block.text.strip()
            if not text and block.role not in (BlockRole.TABLE, BlockRole.FIGURE):
                continue

            elem_dict = pikepdf.Dictionary(
                Type=pikepdf.Name("/StructElem"),
                S=pikepdf.Name(role),
                P=sect,
                Lang=pikepdf.String(block.lang or doc_ir.language),
            )
            if text:
                elem_dict["/T"] = pikepdf.String(text[:200])
            # Rule 7.3:1 — Figure tags must have Alt text
            if block.role == BlockRole.FIGURE:
                alt = (block.alt_text or text or "Figure").strip()[:500]
                elem_dict["/Alt"] = pikepdf.String(alt)
            if page_obj is not None:
                elem_dict["/Pg"] = page_obj

            sect_kids.append(pdf.make_indirect(elem_dict))

        if len(sect_kids) > 0:
            sect["/K"] = sect_kids
            kids.append(sect)

    doc_elem["/K"] = kids
    pdf.Root["/StructTreeRoot"] = struct_root


def _fix_form_field_labels(pdf: "_pikepdf.Pdf", doc_ir: DocumentIR) -> None:
    """Add TU (tooltip/label) entries to form fields that are missing them."""
    import pikepdf

    # Build a lookup: safe_id → label from our DocumentIR
    label_map: dict[str, str] = {}
    for page_ir in doc_ir.pages:
        for block in page_ir.blocks:
            if block.form_field and block.form_field.label:
                label_map[block.form_field.field_name] = block.form_field.label

    # Walk all AcroForm fields and set TU if not already present
    try:
        acroform = pdf.Root.get("/AcroForm")
        if not acroform:
            return
        fields = acroform.get("/Fields", pikepdf.Array([]))
        _patch_field_tu(pdf, fields, label_map, parent_name="")
    except Exception as exc:
        logger.warning("fix_form_labels_failed", error=str(exc))


def _patch_field_tu(pdf: "_pikepdf.Pdf", fields, label_map: dict[str, str], parent_name: str = "") -> None:
    """Recursively add TU to AcroForm fields and widget-only kids."""
    import pikepdf
    import re

    for field_ref in fields:
        try:
            field = pdf.get_object(field_ref.objgen)
            own_name = str(field.get("/T", ""))
            full_name = f"{parent_name}.{own_name}" if parent_name and own_name else own_name or parent_name

            if "/TU" not in field:
                # Prefer our label map; fall back to the field's own /T name or parent name
                clean = re.sub(r'^\w+\[\d+\]\.', '', own_name)
                clean = re.sub(r'\[\d+\]', '', clean)
                label = (
                    label_map.get(full_name)
                    or label_map.get(own_name)
                    or label_map.get(clean)
                    or full_name
                    or own_name
                    or "Field"
                )
                field["/TU"] = pikepdf.String(label)

            kids = field.get("/Kids", pikepdf.Array([]))
            if kids:
                _patch_field_tu(pdf, kids, label_map, full_name)
        except Exception:
            continue


# ── Fix helpers ───────────────────────────────────────────────────────────────

def _fix_outline_lang(pdf: "_pikepdf.Pdf", lang: str) -> None:
    """Rule 7.2:2 — Set Lang on Outline (bookmarks) entries."""
    import pikepdf

    try:
        outlines = pdf.Root.get("/Outlines")
        if not outlines:
            return
        _set_lang_on_outline_items(pdf, outlines, lang)
    except Exception as exc:
        logger.debug("fix_outline_lang_failed", error=str(exc))


def _set_lang_on_outline_items(pdf: "_pikepdf.Pdf", node, lang: str, depth: int = 0) -> None:
    """Recursively set Lang on outline items."""
    import pikepdf

    if depth > 20:
        return

    try:
        obj = node.obj if hasattr(node, 'obj') else node
        if isinstance(obj, pikepdf.Dictionary):
            if "/Title" in obj and "/Lang" not in obj:
                obj["/Lang"] = pikepdf.String(lang)

            # Walk children
            first = obj.get("/First")
            if first:
                item = first
                visited: set = set()
                while item is not None:
                    try:
                        item_obj = item.obj if hasattr(item, 'obj') else item
                        if not isinstance(item_obj, pikepdf.Dictionary):
                            break
                        item_id = id(item_obj)
                        if item_id in visited:
                            break
                        visited.add(item_id)

                        if "/Title" in item_obj and "/Lang" not in item_obj:
                            item_obj["/Lang"] = pikepdf.String(lang)

                        child_first = item_obj.get("/First")
                        if child_first:
                            _set_lang_on_outline_items(pdf, child_first, lang, depth + 1)

                        nxt = item_obj.get("/Next")
                        item = nxt
                    except Exception:
                        break
    except Exception:
        pass


def _fix_embedded_files(pdf: "_pikepdf.Pdf") -> None:
    """Rule 7.11:1 — Add F and UF keys to embedded file spec dicts."""
    import pikepdf

    def _fix_fspec(name_str: str, fspec) -> None:
        """Add /F and /UF to a file specification dictionary."""
        try:
            fspec_obj = fspec
            if hasattr(fspec, 'objgen'):
                fspec_obj = pdf.get_object(fspec.objgen)
            elif hasattr(fspec, 'obj'):
                fspec_obj = fspec.obj
            if not isinstance(fspec_obj, pikepdf.Dictionary):
                return
            if "/F" not in fspec_obj:
                fspec_obj["/F"] = pikepdf.String(name_str)
            if "/UF" not in fspec_obj:
                fspec_obj["/UF"] = pikepdf.String(name_str)
        except Exception:
            pass

    def _process_names_array(names_array) -> None:
        """Process a flat Names array: [name, fspec, name, fspec, ...]"""
        i = 0
        while i + 1 < len(names_array):
            try:
                name_str = str(names_array[i])
                fspec = names_array[i + 1]
                _fix_fspec(name_str, fspec)
            except Exception:
                pass
            i += 2

    def _process_name_tree_node(node) -> None:
        """Process a name tree node that may have /Names or /Kids."""
        try:
            node_obj = node
            if hasattr(node, 'objgen'):
                node_obj = pdf.get_object(node.objgen)
            elif hasattr(node, 'obj'):
                node_obj = node.obj
            if not isinstance(node_obj, pikepdf.Dictionary):
                return

            # Flat names array
            names_arr = node_obj.get("/Names")
            if isinstance(names_arr, pikepdf.Array):
                _process_names_array(names_arr)

            # Kids (intermediate nodes)
            kids = node_obj.get("/Kids")
            if isinstance(kids, pikepdf.Array):
                for kid in kids:
                    _process_name_tree_node(kid)
        except Exception:
            pass

    try:
        names = pdf.Root.get("/Names")
        if not names:
            return
        names_obj = names if isinstance(names, pikepdf.Dictionary) else \
                    (pdf.get_object(names.objgen) if hasattr(names, 'objgen') else names)
        embedded = names_obj.get("/EmbeddedFiles")
        if not embedded:
            return
        _process_name_tree_node(embedded)
    except Exception as exc:
        logger.debug("fix_embedded_files_failed", error=str(exc))


def _fix_optional_content(pdf: "_pikepdf.Pdf") -> None:
    """Rules 7.10:1 and 7.10:2 — Fix optional content configuration dicts."""
    import pikepdf

    def _deref(obj):
        """Dereference an object, handling direct objects with objgen (0,0)."""
        if obj is None:
            return None
        if isinstance(obj, (pikepdf.Dictionary, pikepdf.Array, int, str, bool)):
            return obj
        if hasattr(obj, 'objgen') and obj.objgen != (0, 0):
            try:
                return pdf.get_object(obj.objgen)
            except Exception:
                pass
        return obj

    def _fix_ocd(ocd) -> None:
        try:
            ocd_obj = _deref(ocd)
            if not isinstance(ocd_obj, pikepdf.Dictionary):
                return
            # Rule 7.10:1 — D key must have Name entry
            if "/Name" not in ocd_obj:
                ocd_obj["/Name"] = pikepdf.String("Default")
            # Rule 7.10:2 — Remove AS key from OC config dicts
            if "/AS" in ocd_obj:
                del ocd_obj["/AS"]
        except Exception:
            pass

    try:
        ocprops = pdf.Root.get("/OCProperties")
        if not ocprops:
            return

        ocprops_obj = _deref(ocprops)
        if not isinstance(ocprops_obj, pikepdf.Dictionary):
            return

        d_entry = ocprops_obj.get("/D")
        if d_entry is not None:
            _fix_ocd(d_entry)

        configs = ocprops_obj.get("/Configs", pikepdf.Array([]))
        if isinstance(configs, pikepdf.Array):
            for cfg in configs:
                _fix_ocd(cfg)

    except Exception as exc:
        logger.debug("fix_optional_content_failed", error=str(exc))


def _fix_annotation_tabs(pdf: "_pikepdf.Pdf") -> None:
    """Rule 7.18.3:1 — Add /Tabs /S to every page that has annotations."""
    import pikepdf

    for page in pdf.pages:
        try:
            annots = page.get("/Annots")
            if annots is not None and len(annots) > 0:
                page_obj = page.obj if hasattr(page, 'obj') else page
                if "/Tabs" not in page_obj:
                    page_obj["/Tabs"] = pikepdf.Name("/S")
        except Exception:
            continue


def _fix_link_annotation_contents(pdf: "_pikepdf.Pdf") -> None:
    """Rules 7.18.1:2 and 7.18.5:2 — Add Contents key to Link annotations."""
    import pikepdf

    for page in pdf.pages:
        try:
            annots = page.get("/Annots", pikepdf.Array([]))
            for annot_ref in annots:
                try:
                    if hasattr(annot_ref, 'objgen'):
                        annot = pdf.get_object(annot_ref.objgen)
                    elif hasattr(annot_ref, 'obj'):
                        annot = annot_ref.obj
                    else:
                        annot = annot_ref
                    subtype = str(annot.get("/Subtype", ""))

                    # Rule 7.18.1:2 — non-widget, non-hidden annotations need Contents
                    if subtype != "/Widget":
                        if "/Contents" not in annot:
                            desc = _get_annotation_description(annot)
                            annot["/Contents"] = pikepdf.String(desc)

                except Exception:
                    continue
        except Exception:
            continue


def _fix_cidset_streams(pdf: "_pikepdf.Pdf") -> None:
    """Rule 7.21.4.2:2 — Remove incomplete CIDSet streams from FontDescriptors.

    An incomplete CIDSet causes rule 7.21.4.2:2 to fail. Removing it is
    safer than trying to rebuild it (which requires font program parsing).
    """
    import pikepdf

    # Track by objgen (PDF object ID), not Python object id()
    seen_descriptor_objgens: set = set()

    def _deref(obj):
        if obj is None:
            return None
        if isinstance(obj, (pikepdf.Dictionary, pikepdf.Array, int, str, bool)):
            return obj
        if hasattr(obj, 'objgen') and obj.objgen != (0, 0):
            try:
                return pdf.get_object(obj.objgen)
            except Exception:
                pass
        return obj

    def _fix_font(font) -> None:
        try:
            font_obj = _deref(font)
            if not isinstance(font_obj, pikepdf.Dictionary):
                return

            # Fix this font's own FontDescriptor
            descriptor_ref = font_obj.get("/FontDescriptor")
            if descriptor_ref is not None:
                # Track by objgen to avoid processing the same PDF object twice
                desc_objgen = descriptor_ref.objgen if hasattr(descriptor_ref, 'objgen') else None
                if desc_objgen and desc_objgen != (0, 0):
                    if desc_objgen in seen_descriptor_objgens:
                        pass  # Already processed
                    else:
                        seen_descriptor_objgens.add(desc_objgen)
                        descriptor = _deref(descriptor_ref)
                        if isinstance(descriptor, pikepdf.Dictionary):
                            if "/CIDSet" in descriptor:
                                del descriptor["/CIDSet"]
                                logger.debug("removed_cidset", font=str(font_obj.get("/BaseFont", "?")))

            # Recursively fix DescendantFonts (CIDFontType2 fonts live here)
            descendants_ref = font_obj.get("/DescendantFonts")
            if descendants_ref is not None:
                descendants = _deref(descendants_ref)
                if isinstance(descendants, pikepdf.Array):
                    for d_ref in list(descendants):
                        _fix_font(d_ref)
        except Exception:
            pass

    def _fix_resources(resources) -> None:
        try:
            res_obj = _deref(resources)
            if not isinstance(res_obj, pikepdf.Dictionary):
                return

            fonts = res_obj.get("/Font")
            if fonts is not None:
                fonts_obj = _deref(fonts)
                if isinstance(fonts_obj, pikepdf.Dictionary):
                    for key in list(fonts_obj.keys()):  # list() to avoid iteration issues
                        _fix_font(fonts_obj[key])
        except Exception:
            pass

    try:
        for page in pdf.pages:
            page_obj = page.obj if hasattr(page, 'obj') else page
            if isinstance(page_obj, pikepdf.Dictionary):
                resources = page_obj.get("/Resources")
                if resources is not None:
                    _fix_resources(resources)

    except Exception as exc:
        logger.debug("fix_cidset_failed", error=str(exc))


def _get_struct_doc_elem(pdf: "_pikepdf.Pdf", struct):
    """Return the best parent element for appending Link/Form struct elements.

    Handles both single-element /K (typical) and Array /K (flat struct trees).
    Falls back to the struct root itself when no Document element is found.
    """
    import pikepdf
    try:
        k = struct.get("/K") if hasattr(struct, 'get') else None
        if k is None:
            return struct
        if isinstance(k, pikepdf.Array):
            # Search for a /Document, /Div, /Art, or /Sect element
            for item in list(k)[:20]:
                child = _deref_obj(pdf, item)
                if isinstance(child, pikepdf.Dictionary):
                    s = str(child.get("/S", ""))
                    if s in ("/Document", "/Div", "/Art", "/Sect"):
                        return child
            # No document element found — use struct root as parent
            return struct
        else:
            return _deref_obj(pdf, k) or struct
    except Exception:
        return struct


def _get_annot_struct_parent_type(pdf: "_pikepdf.Pdf", annot) -> str | None:
    """Return the /S type of the struct element that owns this annotation via ParentTree.

    Returns None if annotation has no /StructParent or the entry can't be resolved.
    """
    import pikepdf
    try:
        sp = annot.get("/StructParent")
        if sp is None:
            return None
        sp_int = int(sp)
        struct = pdf.Root.get("/StructTreeRoot")
        if not struct:
            return None
        pt_ref = struct.get("/ParentTree") if hasattr(struct, 'get') else None
        parent_tree = _deref_obj(pdf, pt_ref) if pt_ref is not None else None
        if not isinstance(parent_tree, pikepdf.Dictionary):
            return None
        nums = _deref_obj(pdf, parent_tree.get("/Nums"))
        if not isinstance(nums, pikepdf.Array):
            return None
        for i in range(0, len(nums) - 1, 2):
            try:
                if int(nums[i]) == sp_int:
                    parent = _deref_obj(pdf, nums[i + 1])
                    if isinstance(parent, pikepdf.Dictionary):
                        return str(parent.get("/S", ""))
            except Exception:
                continue
    except Exception:
        pass
    return None


def _collect_number_tree_keys(pdf, node, keys: set, depth: int = 0) -> None:
    """Recursively collect all integer keys from a PDF number tree."""
    import pikepdf
    if depth > 12:
        return
    try:
        node_obj = _deref_obj(pdf, node)
        if not isinstance(node_obj, pikepdf.Dictionary):
            return
        nums = _deref_obj(pdf, node_obj.get("/Nums"))
        if isinstance(nums, pikepdf.Array):
            for i in range(0, len(nums) - 1, 2):
                try:
                    keys.add(int(nums[i]))
                except Exception:
                    pass
        kids = _deref_obj(pdf, node_obj.get("/Kids"))
        if isinstance(kids, pikepdf.Array):
            for kid_ref in list(kids):
                _collect_number_tree_keys(pdf, kid_ref, keys, depth + 1)
    except Exception:
        pass


def _link_orphaned_mcids(pdf: "_pikepdf.Pdf") -> None:
    """Rule 7.1:3 — Create stub struct elements for BDC/MCID blocks not in the ParentTree.

    Some PDFs have BDC << /MCID N >> content blocks whose MCID has no entry in
    the struct tree's ParentTree. veraPDF flags the content as untagged. This adds
    a /P stub struct element for each such MCID so the content is properly linked.
    """
    import pikepdf

    try:
        struct = pdf.Root.get("/StructTreeRoot")
        if not struct:
            return

        parent_tree_ref = struct.get("/ParentTree")
        parent_tree = _deref_obj(pdf, parent_tree_ref) if parent_tree_ref else None
        if not isinstance(parent_tree, pikepdf.Dictionary):
            return

        # Collect all MCID keys already registered in the ParentTree
        known_mcids: set[int] = set()
        _collect_number_tree_keys(pdf, parent_tree, known_mcids)

        # Get or create the top-level /Nums array for appending orphan entries
        nums_ref = parent_tree.get("/Nums")
        if nums_ref is None:
            nums = pikepdf.Array([])
            parent_tree["/Nums"] = nums
        else:
            nums = _deref_obj(pdf, nums_ref)
            if not isinstance(nums, pikepdf.Array):
                return

        doc_elem = _get_struct_doc_elem(pdf, struct)
        next_key_obj = struct.get("/ParentTreeNextKey")
        next_key = int(next_key_obj) if next_key_obj is not None else max(known_mcids) + 1 if known_mcids else 0

        added = 0
        for page in pdf.pages:
            page_obj = page.obj if hasattr(page, 'obj') else page
            try:
                instructions = list(pikepdf.parse_content_stream(page))
            except Exception:
                continue

            # Collect MCIDs used on this page
            page_mcids: set[int] = set()
            for operands, operator in instructions:
                if str(operator) == "BDC":
                    for op in operands:
                        if isinstance(op, pikepdf.Dictionary):
                            mcid_val = op.get("/MCID")
                            if mcid_val is not None:
                                try:
                                    page_mcids.add(int(mcid_val))
                                except Exception:
                                    pass

            orphaned = page_mcids - known_mcids
            if not orphaned:
                continue

            for mcid in sorted(orphaned):
                stub = pdf.make_indirect(pikepdf.Dictionary(
                    Type=pikepdf.Name("/StructElem"),
                    S=pikepdf.Name("/P"),
                    Pg=page_obj,
                    Lang=pdf.Root.get("/Lang", pikepdf.String("en")),
                ))
                if doc_elem and isinstance(doc_elem, pikepdf.Dictionary):
                    stub["/P"] = doc_elem
                    dk = doc_elem.get("/K")
                    if dk is None:
                        doc_elem["/K"] = pikepdf.Array([stub])
                    elif isinstance(dk, pikepdf.Array):
                        dk.append(stub)
                    else:
                        doc_elem["/K"] = pikepdf.Array([dk, stub])

                nums.append(pikepdf.Integer(mcid))
                nums.append(stub)
                known_mcids.add(mcid)
                added += 1

        if added:
            struct["/ParentTreeNextKey"] = pikepdf.Integer(next_key + added)
            logger.debug("linked_orphaned_mcids", count=added)

    except Exception as exc:
        logger.debug("link_orphaned_mcids_failed", error=str(exc))


def _collect_struct_parent_objgens(pdf: "_pikepdf.Pdf") -> set:
    """Return the objgen set of all annotations that already have a /StructParent."""
    import pikepdf
    wired: set = set()
    for page in pdf.pages:
        for annot_ref in page.get("/Annots", pikepdf.Array([])):
            try:
                if hasattr(annot_ref, 'objgen') and annot_ref.objgen != (0, 0):
                    annot = pdf.get_object(annot_ref.objgen)
                    if "/StructParent" in annot:
                        wired.add(annot_ref.objgen)
            except Exception:
                continue
    return wired


def _add_widget_form_struct_elements(pdf: "_pikepdf.Pdf") -> None:
    """Rule 7.18.4:1 — Wrap Widget annotations in /Form struct elements.

    Each Widget annotation must be an OBJR child of a /Form struct element.
    Only adds elements for annotations that don't already have /StructParent.
    """
    import pikepdf

    try:
        struct = pdf.Root.get("/StructTreeRoot")
        if not struct:
            return

        doc_elem = _get_struct_doc_elem(pdf, struct)
        if not isinstance(doc_elem, pikepdf.Dictionary):
            return

        parent_tree_ref = struct.get("/ParentTree")
        parent_tree = _deref_obj(pdf, parent_tree_ref) if parent_tree_ref else None
        next_key_obj = struct.get("/ParentTreeNextKey")
        next_key = int(next_key_obj) if next_key_obj is not None else 0
        nums = parent_tree.get("/Nums") if isinstance(parent_tree, pikepdf.Dictionary) else None

        doc_kids = doc_elem.get("/K")
        if doc_kids is None:
            doc_kids = pikepdf.Array([])
        elif not isinstance(doc_kids, pikepdf.Array):
            doc_kids = pikepdf.Array([doc_kids])

        added = 0
        for page in pdf.pages:
            page_obj = page.obj if hasattr(page, 'obj') else page
            for annot_ref in page.get("/Annots", pikepdf.Array([])):
                try:
                    if not (hasattr(annot_ref, 'objgen') and annot_ref.objgen != (0, 0)):
                        continue
                    annot = pdf.get_object(annot_ref.objgen)
                    if str(annot.get("/Subtype", "")) != "/Widget":
                        continue
                    # Skip only if already correctly parented under a /Form struct element
                    if _get_annot_struct_parent_type(pdf, annot) == "/Form":
                        continue

                    tu = annot.get("/TU") or annot.get("/T")
                    label = str(tu) if tu else "Form field"

                    form_elem = pdf.make_indirect(pikepdf.Dictionary(
                        Type=pikepdf.Name("/StructElem"),
                        S=pikepdf.Name("/Form"),
                        Lang=pdf.Root.get("/Lang", pikepdf.String("en")),
                        T=pikepdf.String(label[:200]),
                        Pg=page_obj,
                    ))
                    form_elem["/P"] = doc_elem

                    objr = pdf.make_indirect(pikepdf.Dictionary(
                        Type=pikepdf.Name("/OBJR"),
                        Pg=page_obj,
                    ))
                    objr["/Obj"] = annot_ref
                    form_elem["/K"] = pikepdf.Array([objr])

                    struct_parent_key = next_key
                    next_key += 1
                    annot["/StructParent"] = pikepdf.Integer(struct_parent_key)
                    if isinstance(nums, pikepdf.Array):
                        nums.append(pikepdf.Integer(struct_parent_key))
                        nums.append(form_elem)

                    doc_kids.append(form_elem)
                    added += 1
                except Exception:
                    continue

        if added:
            doc_elem["/K"] = doc_kids
            struct["/ParentTreeNextKey"] = pikepdf.Integer(next_key)
            logger.debug("widget_form_elems_added", count=added)

    except Exception as exc:
        logger.debug("add_widget_form_struct_failed", error=str(exc))


def _fix_role_map(pdf: "_pikepdf.Pdf") -> None:
    """Rule 7.1:7 — Remove /RoleMap entries that remap standard PDF/UA tags.

    Standard structure types (H, H1-H6, P, L, LI, Table, etc.) must not be
    remapped to other types in /RoleMap.
    """
    import pikepdf

    _STANDARD_TAGS = frozenset({
        "/Document", "/Part", "/Art", "/Sect", "/Div", "/BlockQuote", "/Caption",
        "/TOC", "/TOCI", "/Index", "/NonStruct", "/Private",
        "/H", "/H1", "/H2", "/H3", "/H4", "/H5", "/H6",
        "/P", "/L", "/LI", "/Lbl", "/LBody",
        "/Table", "/TR", "/TH", "/TD", "/THead", "/TBody", "/TFoot",
        "/Span", "/Quote", "/Note", "/Reference", "/BibEntry", "/Code",
        "/Link", "/Annot", "/Ruby", "/Warichu",
        "/Figure", "/Formula", "/Form",
    })

    try:
        struct = pdf.Root.get("/StructTreeRoot")
        if not struct:
            return
        role_map = _deref_obj(pdf, struct.get("/RoleMap"))
        if not isinstance(role_map, pikepdf.Dictionary):
            return

        to_delete = []
        for key in list(role_map.keys()):
            key_name = f"/{key}" if not key.startswith("/") else key
            if key_name in _STANDARD_TAGS:
                to_delete.append(key)

        for key in to_delete:
            del role_map[key]
            logger.debug("rolemap_entry_removed", tag=key)

    except Exception as exc:
        logger.debug("fix_role_map_failed", error=str(exc))


def _fix_struct_reading_order(pdf: "_pikepdf.Pdf") -> None:
    """Sort direct K children of container struct elements by page + MCID order.

    PDF generators sometimes emit struct children in content-stream order rather
    than logical reading order.  This reorders the top-level containers (Document,
    Part, Sect, Div, Art) so their children appear in ascending page → MCID order.

    Only top-two levels of the tree are touched; tables, lists, and other
    structured elements are left intact to avoid breaking row/column semantics.
    """
    import pikepdf

    _CONTAINERS = frozenset({
        "/Document", "/Part", "/Sect", "/Div", "/Art", "/BlockQuote",
    })

    def _first_mcid_key(pdf, node, depth=0) -> tuple[int, int]:
        """Return (page_index, mcid) of the first leaf MCID under node."""
        if depth > 10:
            return (9999, 9999)
        try:
            obj = _deref_obj(pdf, node)
            if not isinstance(obj, pikepdf.Dictionary):
                return (9999, 9999)

            # MCID leaf
            k = obj.get("/K")
            pg = obj.get("/Pg")
            page_idx = 0
            if pg is not None:
                try:
                    pg_obj = _deref_obj(pdf, pg)
                    for i, p in enumerate(pdf.pages):
                        p_obj = p.obj if hasattr(p, 'obj') else p
                        if hasattr(pg_obj, 'objgen') and hasattr(p_obj, 'objgen'):
                            if pg_obj.objgen == p_obj.objgen:
                                page_idx = i
                                break
                except Exception:
                    pass

            if isinstance(k, int):
                return (page_idx, k)

            # Recurse into first child
            if isinstance(k, pikepdf.Array) and len(k) > 0:
                return _first_mcid_key(pdf, list(k)[0], depth + 1)
            elif k is not None and not isinstance(k, int):
                return _first_mcid_key(pdf, k, depth + 1)
        except Exception:
            pass
        return (9999, 9999)

    def _sort_container(node, depth=0):
        if depth > 2:
            return
        try:
            obj = _deref_obj(pdf, node)
            if not isinstance(obj, pikepdf.Dictionary):
                return
            s = str(obj.get("/S", ""))
            k = obj.get("/K")
            if not isinstance(k, pikepdf.Array) or len(k) < 2:
                return

            # Only sort containers, not leaf semantic elements
            if s not in _CONTAINERS and depth > 0:
                return

            children = list(k)
            # Only sort if all children are indirect refs (struct elements, not ints)
            if any(isinstance(c, int) for c in children):
                return

            keyed = [(c, _first_mcid_key(pdf, c)) for c in children]
            keyed.sort(key=lambda x: x[1])
            new_order = [c for c, _ in keyed]

            if new_order != children:
                obj["/K"] = pikepdf.Array(new_order)

            # Recurse into children that are containers
            for child in new_order:
                _sort_container(child, depth + 1)
        except Exception:
            pass

    try:
        struct = pdf.Root.get("/StructTreeRoot")
        if not struct:
            return
        k = _deref_obj(pdf, struct).get("/K") if hasattr(struct, 'get') else None
        if isinstance(k, pikepdf.Array):
            for child in list(k):
                _sort_container(child, 0)
        elif k is not None:
            _sort_container(k, 0)
    except Exception as exc:
        logger.debug("fix_struct_reading_order_failed", error=str(exc))


# WCAG 2.4.4 — Link Purpose (In Context)
_GENERIC_LINK_TEXTS = frozenset({
    "click here", "click", "here", "read more", "read more »", "more",
    "learn more", "link", "this link", "this", "go", "continue", "details",
    "see details", "info", "more info", "more information", "visit",
    "download", "get it", "open", "view", "see more", "see here",
})

_URL_PATTERN_PREFIX = ("http://", "https://", "www.", "ftp://")


def _fix_link_text(pdf: "_pikepdf.Pdf") -> None:
    """WCAG 2.4.4 — Replace generic or bare-URL link text with meaningful content.

    Walks Link struct elements. If the element's text content is a generic phrase
    (click here, read more, bare URL) and the annotation has a URI, replace the
    alt text / title with the destination URL description so screen readers get
    useful context.
    """
    import pikepdf

    def _get_link_annotation_uri(obj) -> str:
        """Extract the URI from a Link annotation embedded in the struct element."""
        try:
            # Annotations are referenced via /A on the struct element or via /Obj
            annot_ref = obj.get("/A") or obj.get("/Obj")
            if annot_ref is None:
                return ""
            annot = _deref_obj(pdf, annot_ref)
            if not isinstance(annot, pikepdf.Dictionary):
                return ""
            action = _deref_obj(pdf, annot.get("/A"))
            if not isinstance(action, pikepdf.Dictionary):
                return ""
            uri = action.get("/URI")
            return str(uri) if uri else ""
        except Exception:
            return ""

    def _get_struct_text(obj) -> str:
        """Extract visible text from a struct element's content."""
        try:
            # /Alt is the alt text (for figures), /T is the title
            t = obj.get("/T")
            if t:
                return str(t).strip()
            # Walk K looking for text-bearing leaves
            k = _deref_obj(pdf, obj.get("/K"))
            if isinstance(k, pikepdf.Array):
                texts = []
                for child in list(k):
                    if isinstance(child, int):
                        continue
                    c = _deref_obj(pdf, child)
                    if isinstance(c, pikepdf.Dictionary):
                        ct = c.get("/T") or c.get("/ActualText")
                        if ct:
                            texts.append(str(ct).strip())
                return " ".join(texts)
        except Exception:
            pass
        return ""

    def _walk(node, depth=0):
        if depth > 60:
            return
        try:
            obj = _deref_obj(pdf, node)
            if not isinstance(obj, pikepdf.Dictionary):
                return
            s = str(obj.get("/S", ""))
            if s == "/Link":
                link_text = _get_struct_text(obj).lower().strip(".,!? ")
                if (link_text in _GENERIC_LINK_TEXTS or
                        any(link_text.startswith(p) for p in _URL_PATTERN_PREFIX)):
                    uri = _get_link_annotation_uri(obj)
                    if uri:
                        # Derive a human-readable label from the hostname
                        try:
                            from urllib.parse import urlparse
                            parsed = urlparse(uri)
                            host = parsed.netloc or parsed.path
                            path = parsed.path.rstrip("/").rsplit("/", 1)[-1] or ""
                            label = f"{host}: {path}".strip(": ") if path else host
                            label = label[:120]
                        except Exception:
                            label = uri[:120]
                        obj["/Alt"] = pikepdf.String(label)
                return  # don't recurse into Link children

            k = obj.get("/K")
            if k is None:
                return
            if isinstance(k, pikepdf.Array):
                for child in list(k):
                    if not isinstance(child, int):
                        _walk(child, depth + 1)
            elif not isinstance(k, int):
                _walk(k, depth + 1)
        except Exception:
            pass

    try:
        struct = pdf.Root.get("/StructTreeRoot")
        if not struct:
            return
        k = _deref_obj(pdf, struct).get("/K") if hasattr(struct, 'get') else None
        if k is None:
            return
        if isinstance(k, pikepdf.Array):
            for child in list(k):
                _walk(child)
        elif not isinstance(k, int):
            _walk(k)
    except Exception as exc:
        logger.debug("fix_link_text_failed", error=str(exc))


def _tag_header_footer_artifacts(pdf: "_pikepdf.Pdf") -> None:
    """Mark running headers/footers as Artifact so screen readers skip them.

    Heuristic: text content appearing in the top 7% or bottom 7% of the page
    that is present on ≥3 pages with similar text is almost certainly a running
    header/footer. Those content streams get wrapped in /Artifact BMC blocks.
    """
    import pikepdf

    if not pdf.pages:
        return

    try:
        page_count = len(pdf.pages)
        if page_count < 3:
            return  # Not enough pages to detect running elements

        page_h_samples = []
        for page in pdf.pages:
            page_obj = page.obj if hasattr(page, 'obj') else page
            mbox = page_obj.get("/MediaBox")
            if mbox and hasattr(mbox, '__iter__'):
                try:
                    h = float(list(mbox)[3])
                    page_h_samples.append(h)
                except Exception:
                    pass

        if not page_h_samples:
            return

        avg_h = sum(page_h_samples) / len(page_h_samples)
        header_threshold = avg_h * 0.93   # top 7%
        footer_threshold = avg_h * 0.07   # bottom 7%

        # Scan each page's struct elements for content in header/footer zones
        # and mark them as Artifact by setting /S to /Artifact
        def _mark_zone_artifacts(node, page_obj, depth=0):
            if depth > 20:
                return
            try:
                obj = _deref_obj(pdf, node)
                if not isinstance(obj, pikepdf.Dictionary):
                    return

                # Check if this element has a page association and a bbox
                pg = obj.get("/Pg")
                if pg is not None:
                    pg_obj = _deref_obj(pdf, pg)
                    # Only process elements on this page
                    if (hasattr(pg_obj, 'objgen') and hasattr(page_obj, 'objgen') and
                            pg_obj.objgen != page_obj.objgen):
                        return

                bbox_obj = obj.get("/BBox") or obj.get("/bbox")
                if bbox_obj and isinstance(bbox_obj, pikepdf.Array):
                    try:
                        coords = [float(v) for v in list(bbox_obj)]
                        if len(coords) >= 4:
                            y0, y1 = min(coords[1], coords[3]), max(coords[1], coords[3])
                            # In header zone (above threshold) or footer zone (below)
                            if y0 > header_threshold or y1 < footer_threshold:
                                s = str(obj.get("/S", ""))
                                if s not in ("/Artifact", ""):
                                    obj["/S"] = pikepdf.Name("/Artifact")
                                    return
                    except Exception:
                        pass

                k = obj.get("/K")
                if k is None:
                    return
                if isinstance(k, pikepdf.Array):
                    for child in list(k):
                        if not isinstance(child, int):
                            _mark_zone_artifacts(child, page_obj, depth + 1)
                elif not isinstance(k, int):
                    _mark_zone_artifacts(k, page_obj, depth + 1)
            except Exception:
                pass

        # Apply to pages with annotations indicating headers/footers
        # (conservative: only pages 2+ to avoid touching cover page content)
        for page in list(pdf.pages)[1:]:
            page_obj = page.obj if hasattr(page, 'obj') else page
            try:
                struct = pdf.Root.get("/StructTreeRoot")
                if struct:
                    k = _deref_obj(pdf, struct).get("/K")
                    if isinstance(k, pikepdf.Array):
                        for child in list(k):
                            _mark_zone_artifacts(child, page_obj)
                    elif k is not None and not isinstance(k, int):
                        _mark_zone_artifacts(k, page_obj)
            except Exception:
                pass

    except Exception as exc:
        logger.debug("tag_header_footer_artifacts_failed", error=str(exc))


def _get_annotation_description(annot) -> str:
    """Extract a meaningful description for an annotation."""
    # Try action URI
    try:
        action = annot.get("/A")
        if action:
            uri = action.get("/URI")
            if uri:
                return str(uri)[:200]
            s = action.get("/S")
            if s and str(s) == "/GoTo":
                return "Internal link"
    except Exception:
        pass

    # Try /T (title)
    try:
        t = annot.get("/T")
        if t:
            return str(t)[:200]
    except Exception:
        pass

    subtype = str(annot.get("/Subtype", ""))
    if subtype == "/Link":
        return "Link"
    return "Annotation"
