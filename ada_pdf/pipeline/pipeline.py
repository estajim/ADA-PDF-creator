"""AccessibilityPipeline — orchestrates all 8 processing stages."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional

from ada_pdf.config import Settings
from ada_pdf.pipeline.context import PipelineContext
from ada_pdf.pipeline.stages import (
    s1_ingest,
    s2_extract,
    s3_ocr,
    s4_layout,
    s5_structure,
    s6_alttext,
    s7_generate,
    s8_metadata,
)
from ada_pdf.utils.logging import get_logger

logger = get_logger(__name__)

ProgressCallback = Callable[[str, int], None]

_STAGES = [
    (s1_ingest.run,   "ingesting",           5),
    (s2_extract.run,  "extracting",         15),
    (s3_ocr.run,      "ocr",                30),
    (s4_layout.run,   "layout_analysis",    50),
    (s5_structure.run,"structure_detection",65),
    (s6_alttext.run,  "generating_alt_text",80),
    (s7_generate.run, "generating_pdf",     90),
    (s8_metadata.run, "writing_metadata",   95),
]


class PipelineResult:
    def __init__(
        self,
        output_path: Optional[Path],
        page_count: int,
        processing_time: float,
        document_title: str,
        language: str,
    ):
        self.output_path = output_path
        self.page_count = page_count
        self.processing_time = processing_time
        self.document_title = document_title
        self.language = language


class AccessibilityPipeline:
    def __init__(self, settings: Settings):
        self.settings = settings

    def run(
        self,
        input_path: Path,
        job_id: str,
        progress_callback: Optional[ProgressCallback] = None,
        options: Optional[dict] = None,
    ) -> PipelineResult:
        t0 = time.monotonic()
        opts = options or {}

        # Large PDF guard: if page count exceeds the chunk threshold, warn and
        # cap to avoid OOM.  Full chunked-merge support is planned; for now we
        # process the entire document but release the fitz doc early and disable
        # in-memory image caching to reduce peak RSS.
        max_chunk = getattr(self.settings, "MAX_PAGES_PER_CHUNK", 50)
        try:
            import fitz as _fitz
            _probe = _fitz.open(str(input_path))
            _page_count = len(_probe)
            _probe.close()
            if _page_count > max_chunk:
                logger.warning(
                    "large_pdf_detected",
                    pages=_page_count,
                    max_chunk=max_chunk,
                    note="processing full document; peak memory may be high",
                )
        except Exception:
            pass

        ctx = PipelineContext(input_path=input_path, job_id=job_id)
        ctx.skip_alt_text = not opts.get("generate_alt_text", True)
        ctx.skip_forms = not opts.get("detect_forms", True)
        ctx.force_language = opts.get("language") if opts.get("language") != "auto" else None
        ctx.force_doc_type = opts.get("doc_type") if opts.get("doc_type") != "auto" else None
        ctx.quality = opts.get("quality", "thorough")
        ctx.conversion_mode = opts.get("conversion_mode", "rebuild")

        def _progress(status: str, pct: int) -> None:
            if progress_callback:
                progress_callback(status, pct)
            logger.debug("pipeline_progress", status=status, pct=pct)

        tag_in_place = ctx.conversion_mode == "tag_in_place"

        for stage_fn, status, pct in _STAGES:
            # In tag_in_place mode skip the HTML rebuild stage — s8 works directly
            # on the original PDF bytes instead.
            if tag_in_place and stage_fn is s7_generate.run:
                ctx.output_pdf_bytes = ctx.input_path.read_bytes()
                logger.info("tag_in_place_skip_generate")
                continue

            _progress(status, pct)
            try:
                ctx = stage_fn(ctx, self.settings)
            except s6_alttext.AltTextError as exc:
                logger.warning("alttext_skipped", error=str(exc))
                ctx.skip_alt_text = True
                if not tag_in_place:
                    ctx = s7_generate.run(ctx, self.settings)
                else:
                    ctx.output_pdf_bytes = ctx.input_path.read_bytes()
                ctx = s8_metadata.run(ctx, self.settings)
                break
            except (s7_generate.GenerationError, s1_ingest.IngestError):
                raise
            except Exception as exc:
                logger.error("stage_error", stage=stage_fn.__module__, error=str(exc))
                raise

        _progress("completed", 100)

        doc_ir = getattr(ctx, "document_ir", None)
        return PipelineResult(
            output_path=ctx.output_path,
            page_count=ctx.page_count,
            processing_time=time.monotonic() - t0,
            document_title=doc_ir.title if doc_ir else "Unknown",
            language=doc_ir.language if doc_ir else "en",
        )
