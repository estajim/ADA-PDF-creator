"""Mutable state passed through all 8 pipeline stages."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class RawPage:
    page_number: int
    width: float
    height: float
    raw_fitz_dict: Optional[dict] = None       # fitz get_text("dict") output
    image_bytes: Optional[bytes] = None         # rasterized PNG for scanned pages
    ocr_blocks: list[dict] = field(default_factory=list)  # Surya/Tesseract output


@dataclass
class PipelineContext:
    input_path: Path
    job_id: str
    raw_pages: list[RawPage] = field(default_factory=list)
    layout_data: list[Any] = field(default_factory=list)   # Docling layout output per page
    skip_alt_text: bool = False
    skip_forms: bool = False
    force_language: Optional[str] = None
    force_doc_type: Optional[str] = None
    quality: str = "thorough"
    conversion_mode: str = "rebuild"            # "rebuild" | "tag_in_place"
    output_pdf_bytes: Optional[bytes] = None
    output_path: Optional[Path] = None
    page_count: int = 0

    # Imported lazily to avoid top-level pymupdf import cost on API workers
    _fitz_doc: Any = field(default=None, repr=False)

    @property
    def fitz_doc(self) -> Any:
        return self._fitz_doc

    @fitz_doc.setter
    def fitz_doc(self, value: Any) -> None:
        self._fitz_doc = value
