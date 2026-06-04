"""Embed open-source / system font substitutes for non-embedded Standard 14 fonts.

Rules fixed:
  7.21.4.1:1 — font programs must be embedded
  7.21.7:1   — fonts must have a ToUnicode CMap

Usage::

    import pikepdf
    from ada_pdf.pipeline.algorithms.font_embedder import embed_missing_fonts

    pdf = pikepdf.open("in.pdf")
    names = embed_missing_fonts(pdf)
    pdf.save("out.pdf")
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import NamedTuple

from ada_pdf.utils.logging import get_logger

logger = get_logger(__name__)


# ── Font catalogue ─────────────────────────────────────────────────────────
class _FontEntry(NamedTuple):
    path: str       # path to .ttc / .ttf / .otf
    index: int      # face index inside a .ttc (0 for single-face files)


_STD14_MAP: dict[str, _FontEntry] = {
    # Helvetica family  →  macOS Helvetica.ttc
    "Helvetica":           _FontEntry("/System/Library/Fonts/Helvetica.ttc", 0),
    "Helvetica-Bold":      _FontEntry("/System/Library/Fonts/Helvetica.ttc", 1),
    "Helvetica-Oblique":   _FontEntry("/System/Library/Fonts/Helvetica.ttc", 2),
    "Helvetica-BoldOblique": _FontEntry("/System/Library/Fonts/Helvetica.ttc", 3),
    # Times family  →  macOS Times.ttc
    "Times-Roman":         _FontEntry("/System/Library/Fonts/Times.ttc", 0),
    "Times-Bold":          _FontEntry("/System/Library/Fonts/Times.ttc", 1),
    "Times-Italic":        _FontEntry("/System/Library/Fonts/Times.ttc", 2),
    "Times-BoldItalic":    _FontEntry("/System/Library/Fonts/Times.ttc", 3),
    # Courier family  →  macOS Courier.ttc
    "Courier":             _FontEntry("/System/Library/Fonts/Courier.ttc", 0),
    "Courier-Bold":        _FontEntry("/System/Library/Fonts/Courier.ttc", 1),
    "Courier-Oblique":     _FontEntry("/System/Library/Fonts/Courier.ttc", 2),
    "Courier-BoldOblique": _FontEntry("/System/Library/Fonts/Courier.ttc", 3),
    # Arial aliases (common non-embedded name in exported PDFs)
    "Arial":               _FontEntry("/System/Library/Fonts/Helvetica.ttc", 0),
    "Arial-BoldMT":        _FontEntry("/System/Library/Fonts/Helvetica.ttc", 1),
    "Arial-ItalicMT":      _FontEntry("/System/Library/Fonts/Helvetica.ttc", 2),
    "Arial-BoldItalicMT":  _FontEntry("/System/Library/Fonts/Helvetica.ttc", 3),
}

# Windows-1252 → Unicode for building ToUnicode CMap
_WIN1252_TO_UNICODE: dict[int, int] = {}
for _code in range(0x20, 0x100):
    try:
        _WIN1252_TO_UNICODE[_code] = ord(bytes([_code]).decode("cp1252"))
    except (UnicodeDecodeError, ValueError):
        pass


# ── Public entry-point ─────────────────────────────────────────────────────

def embed_missing_fonts(pdf: "pikepdf.Pdf") -> list[str]:
    """Embed font programs for every non-embedded Standard 14 font in *pdf*.

    Returns a list of font base-names that were successfully embedded.
    Mutates *pdf* in place — caller is responsible for saving.
    """
    import pikepdf  # local import keeps module importable without pikepdf at load time

    embedded: list[str] = []
    seen: set[str] = set()       # track by base name to embed once per PDF
    cache: dict[str, bytes] = {} # base_name → ttf bytes (avoid re-loading same file)

    for page in pdf.pages:
        _process_resource_fonts(pdf, page, embedded, seen, cache)

    if embedded:
        logger.info("fonts_embedded", count=len(embedded), names=embedded)
    return embedded


# ── Internal helpers ───────────────────────────────────────────────────────

def _process_resource_fonts(
    pdf: "pikepdf.Pdf",
    resource_holder,
    embedded: list[str],
    seen: set[str],
    cache: dict[str, bytes],
) -> None:
    import pikepdf

    try:
        res_ref = resource_holder.get("/Resources")
        if res_ref is None:
            return
        res = res_ref if isinstance(res_ref, pikepdf.Dictionary) else pdf.get_object(res_ref.objgen)
        if not isinstance(res, pikepdf.Dictionary):
            return
        fonts_ref = res.get("/Font")
        if fonts_ref is None:
            return
        fonts = fonts_ref if isinstance(fonts_ref, pikepdf.Dictionary) else pdf.get_object(fonts_ref.objgen)
        if not isinstance(fonts, pikepdf.Dictionary):
            return

        for key in list(fonts.keys()):
            _try_embed_one(pdf, fonts[key], embedded, seen, cache)
    except Exception as exc:
        logger.debug("embed_font_resource_error", error=str(exc))


def _try_embed_one(
    pdf: "pikepdf.Pdf",
    font_ref,
    embedded: list[str],
    seen: set[str],
    cache: dict[str, bytes],
) -> None:
    import pikepdf

    try:
        font = font_ref if isinstance(font_ref, pikepdf.Dictionary) else pdf.get_object(font_ref.objgen)
        if not isinstance(font, pikepdf.Dictionary):
            return

        # Resolve base name — strip subset prefix (e.g. "ABCDEF+Helvetica")
        raw_base = str(font.get("/BaseFont", "")).lstrip("/")
        base = raw_base.split("+")[-1]
        if not base or base in seen:
            return

        # Skip if a font program is already embedded
        desc_ref = font.get("/FontDescriptor")
        if desc_ref is not None:
            try:
                desc = desc_ref if isinstance(desc_ref, pikepdf.Dictionary) \
                       else pdf.get_object(desc_ref.objgen)
                if isinstance(desc, pikepdf.Dictionary) and any(
                    k in desc for k in ("/FontFile", "/FontFile2", "/FontFile3")
                ):
                    seen.add(base)
                    return
            except Exception:
                pass

        entry = _STD14_MAP.get(base)
        if entry is None:
            return
        if not Path(entry.path).exists():
            logger.debug("system_font_not_found", font=base, path=entry.path)
            return

        # Load TTF bytes (cached)
        cache_key = f"{entry.path}:{entry.index}"
        if cache_key not in cache:
            cache[cache_key] = _load_ttf_bytes(entry.path, entry.index)
        ttf_bytes = cache[cache_key]
        if not ttf_bytes:
            return

        # Read metrics from font
        metrics = _read_metrics(entry.path, entry.index)

        # Build FontDescriptor with embedded FontFile2
        font_stream = pdf.make_stream(ttf_bytes)
        font_stream["/Length1"] = pikepdf.Integer(len(ttf_bytes))
        descriptor = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name("/FontDescriptor"),
            FontName=pikepdf.Name("/" + base),
            Flags=pikepdf.Integer(metrics["flags"]),
            FontBBox=pikepdf.Array([pikepdf.Integer(v) for v in metrics["bbox"]]),
            ItalicAngle=pikepdf.Integer(metrics["italic_angle"]),
            Ascent=pikepdf.Integer(metrics["ascent"]),
            Descent=pikepdf.Integer(metrics["descent"]),
            CapHeight=pikepdf.Integer(metrics["cap_height"]),
            XHeight=pikepdf.Integer(metrics["x_height"]),
            StemV=pikepdf.Integer(metrics["stem_v"]),
            FontFile2=pdf.make_indirect(font_stream),
        ))

        font["/FontDescriptor"] = descriptor
        # Promote to TrueType subtype (was Type1 by name only)
        font["/Subtype"] = pikepdf.Name("/TrueType")

        # Add ToUnicode CMap (rule 7.21.7)
        _add_to_unicode(pdf, font)

        seen.add(base)
        embedded.append(base)
        logger.debug("font_embedded", base=base)

    except Exception as exc:
        logger.debug("embed_font_error", error=str(exc))


def _load_ttf_bytes(path: str, face_index: int) -> bytes:
    """Extract a single TrueType face from a .ttc (or plain .ttf) and return bytes."""
    try:
        from fontTools.ttLib import TTCollection, TTFont
        if path.lower().endswith(".ttc"):
            tc = TTCollection(path)
            tt = tc.fonts[face_index]
        else:
            tt = TTFont(path)
        buf = io.BytesIO()
        tt.save(buf)
        return buf.getvalue()
    except Exception as exc:
        logger.debug("load_ttf_failed", path=path, face=face_index, error=str(exc))
        return b""


def _read_metrics(path: str, face_index: int) -> dict:
    """Extract PDF font metrics from a TrueType face."""
    defaults = {
        "ascent": 800, "descent": -200, "cap_height": 700,
        "x_height": 500, "stem_v": 80, "italic_angle": 0,
        "bbox": [-200, -200, 1200, 1000], "flags": 32,
    }
    try:
        from fontTools.ttLib import TTCollection, TTFont
        if path.lower().endswith(".ttc"):
            tc = TTCollection(path)
            tt = tc.fonts[face_index]
        else:
            tt = TTFont(path)

        head = tt["head"]
        hhea = tt["hhea"]
        units = head.unitsPerEm or 1000
        scale = 1000.0 / units

        os2 = tt.get("OS/2")
        ascent  = int(hhea.ascent * scale)
        descent = int(hhea.descent * scale)
        cap_height = int((os2.sCapHeight if os2 and os2.sCapHeight else int(ascent * 0.9)) * scale)
        x_height   = int((os2.sxHeight  if os2 and os2.sxHeight  else int(ascent * 0.6)) * scale)

        # PDF flags: bit 6 (0-indexed) = Nonsymbolic (32), bit 7 = Italic (64)
        flags = 32
        if os2:
            if os2.fsSelection & 0x01:   # Italic bit
                flags |= 64
            if os2.fsSelection & 0x20:   # Bold bit — no PDF flag, skip
                pass

        italic_angle = 0
        if "post" in tt:
            italic_angle = int(tt["post"].italicAngle)

        bbox = [
            int(head.xMin * scale), int(head.yMin * scale),
            int(head.xMax * scale), int(head.yMax * scale),
        ]
        # Reasonable stem width estimate based on weight
        stem_v = 80
        if os2 and os2.usWeightClass >= 700:
            stem_v = 150
        elif os2 and os2.usWeightClass >= 500:
            stem_v = 100

        return {
            "ascent": ascent, "descent": descent,
            "cap_height": cap_height, "x_height": x_height,
            "stem_v": stem_v, "italic_angle": italic_angle,
            "bbox": bbox, "flags": flags,
        }
    except Exception as exc:
        logger.debug("read_metrics_failed", path=path, error=str(exc))
        return defaults


def _add_to_unicode(pdf: "pikepdf.Pdf", font: "pikepdf.Dictionary") -> None:
    """Build and attach a ToUnicode CMap for WinAnsiEncoding (rule 7.21.7).

    Only added if the font uses WinAnsiEncoding or has no explicit encoding.
    Existing ToUnicode entries are left intact.
    """
    import pikepdf

    if "/ToUnicode" in font:
        return

    enc = font.get("/Encoding")
    enc_name = str(enc) if enc else ""
    # Only synthesise for WinAnsi or when encoding is implicit (common for Std14)
    if enc_name not in ("", "/WinAnsiEncoding", "/MacRomanEncoding"):
        return

    mapping = _WIN1252_TO_UNICODE if "WinAnsi" in enc_name or not enc_name else _build_mac_roman()

    lines = [
        "/CIDInit /ProcSet findresource begin",
        "12 dict begin",
        "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /Adobe-Identity-UCS def",
        "/CMapType 2 def",
        "1 begincodespacerange",
        "<20> <FF>",
        "endcodespacerange",
    ]
    pairs = [(code, uval) for code, uval in sorted(mapping.items()) if 0x20 <= code <= 0xFF]
    # Emit in chunks of 100
    for i in range(0, len(pairs), 100):
        chunk = pairs[i : i + 100]
        lines.append(f"{len(chunk)} beginbfchar")
        for code, uval in chunk:
            lines.append(f"<{code:02X}> <{uval:04X}>")
        lines.append("endbfchar")
    lines += ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]

    cmap_bytes = "\n".join(lines).encode("latin-1")
    cmap_stream = pdf.make_stream(cmap_bytes)
    font["/ToUnicode"] = pdf.make_indirect(cmap_stream)


def _build_mac_roman() -> dict[int, int]:
    mapping: dict[int, int] = {}
    for code in range(0x20, 0x100):
        try:
            mapping[code] = ord(bytes([code]).decode("mac_roman"))
        except (UnicodeDecodeError, ValueError):
            pass
    return mapping
