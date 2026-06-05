"""Shared pytest fixtures."""
from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ada_pdf.models.domain import (
    BBox, BlockIR, BlockRole, DocumentIR, PageIR, PageType, SpanIR,
)


# ── Settings ──────────────────────────────────────────────────────────────

@pytest.fixture
def settings(tmp_path):
    from ada_pdf.config import Settings, OCRProvider, ColumnAlgorithm
    return Settings(
        DATABASE_URL="sqlite+aiosqlite:///./test.db",
        REDIS_URL="redis://localhost:6379/15",
        CELERY_BROKER_URL="redis://localhost:6379/15",
        CELERY_RESULT_BACKEND="redis://localhost:6379/15",
        ANTHROPIC_API_KEY="",
        STORAGE_ROOT=tmp_path / "storage",
        MAX_FILE_SIZE_MB=10,
        VERAPDF_CLI_PATH="/opt/verapdf/verapdf",
        OCR_PRIMARY=OCRProvider.SURYA,
        WEASYPRINT_ENABLED=True,
        REPORTLAB_FALLBACK=True,
        COLUMN_ALGORITHM=ColumnAlgorithm.GAP,
    )


# ── Block / page helpers ───────────────────────────────────────────────────

def make_block(
    text: str = "Hello",
    role: BlockRole = BlockRole.P,
    x0: float = 50, y0: float = 100,
    x1: float = 300, y1: float = 120,
    page: int = 1,
    font_size: float = 12.0,
    is_bold: bool = False,
    is_italic: bool = False,
    is_artifact: bool = False,
) -> BlockIR:
    bbox = BBox(x0=x0, y0=y0, x1=x1, y1=y1, page_number=page)
    span = SpanIR(text=text, font_size=font_size, is_bold=is_bold, is_italic=is_italic)
    return BlockIR(role=role, bbox=bbox, spans=[span], is_artifact=is_artifact)


def make_page(blocks: list[BlockIR], width: float = 612, height: float = 792) -> PageIR:
    return PageIR(
        page_number=1,
        width=width,
        height=height,
        page_type=PageType.DIGITAL,
        blocks=blocks,
    )


@pytest.fixture
def simple_page():
    return make_page([
        make_block("Title", font_size=24, y0=50, y1=80),
        make_block("Body text paragraph.", font_size=12, y0=100, y1=120),
    ])


@pytest.fixture
def two_column_page():
    """Page with two columns of text."""
    left_blocks = [
        make_block("Left col para 1", x0=50, x1=250, y0=100, y1=120),
        make_block("Left col para 2", x0=50, x1=250, y0=130, y1=150),
    ]
    right_blocks = [
        make_block("Right col para 1", x0=320, x1=560, y0=100, y1=120),
        make_block("Right col para 2", x0=320, x1=560, y0=130, y1=150),
    ]
    return make_page(left_blocks + right_blocks)
