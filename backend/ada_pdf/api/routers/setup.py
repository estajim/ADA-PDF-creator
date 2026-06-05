"""First-run setup routes.

GET  /setup        → serves setup.html
GET  /setup/stream → SSE stream of install progress
GET  /setup/status → JSON {ready: bool}
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse

from ada_pdf.setup.installer import is_ml_ready, run_setup
from ada_pdf.utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["setup"])

_STATIC = Path(__file__).parent.parent.parent / "static"


@router.get("/setup", include_in_schema=False)
async def setup_page():
    return FileResponse(str(_STATIC / "setup.html"))


@router.get("/setup/status", include_in_schema=False)
async def setup_status():
    return JSONResponse({"ready": is_ml_ready()})


@router.get("/setup/stream", include_in_schema=False)
async def setup_stream():
    """SSE endpoint — streams install progress line by line."""

    async def _generate():
        try:
            async for line in run_setup():
                # SSE format: "data: <text>\n\n"
                # Escape newlines so each line is one SSE message
                safe = line.rstrip("\n").replace("\n", " ")
                yield f"data: {safe}\n\n"
        except Exception as exc:
            logger.exception("setup_stream_error", error=str(exc))
            yield f"data: ERROR: {exc}\n\n"

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx buffering
        },
    )
