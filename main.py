"""PyInstaller entry point — starts the FastAPI server and opens the browser.

Run directly:  python main.py
PyInstaller:   pyinstaller ada_pdf.spec
"""
from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

# ── Ensure the repo root is on sys.path when running from a PyInstaller bundle
if getattr(sys, "frozen", False):
    # Running as PyInstaller bundle — _MEIPASS is the temp extraction dir
    BASE_DIR = Path(sys._MEIPASS)          # type: ignore[attr-defined]
    RUNTIME_DIR = Path(sys.executable).parent   # directory next to the .exe
else:
    BASE_DIR = Path(__file__).parent
    RUNTIME_DIR = BASE_DIR

sys.path.insert(0, str(BASE_DIR))

# ── Environment defaults for SIMPLE_MODE ─────────────────────────────────
_DB_PATH = RUNTIME_DIR / "ada_pdf.db"
_STORAGE = RUNTIME_DIR / "storage"
_VERAPDF = RUNTIME_DIR / "verapdf" / "verapdf"       # bundled veraPDF CLI

os.environ.setdefault("SIMPLE_MODE",      "true")
os.environ.setdefault("DATABASE_URL",     f"sqlite+aiosqlite:///{_DB_PATH}")
os.environ.setdefault("STORAGE_ROOT",     str(_STORAGE))
os.environ.setdefault("VERAPDF_CLI_PATH", str(_VERAPDF))
os.environ.setdefault("REDIS_URL",        "redis://localhost:6379/0")   # unused in simple mode
os.environ.setdefault("CELERY_BROKER_URL","redis://localhost:6379/1")   # unused in simple mode
os.environ.setdefault("CELERY_RESULT_BACKEND","redis://localhost:6379/2")
os.environ.setdefault("ANTHROPIC_API_KEY","")
os.environ.setdefault("CORS_ALLOWED_ORIGINS","*")
os.environ.setdefault("RATE_LIMIT_PER_MINUTE","600")

PORT = int(os.environ.get("ADA_PDF_PORT", "8765"))
URL  = f"http://127.0.0.1:{PORT}"


def _open_browser() -> None:
    time.sleep(1.8)
    webbrowser.open(URL)


def main() -> None:
    print(f"ADA PDF Converter — starting on {URL}")
    threading.Thread(target=_open_browser, daemon=True).start()

    # Import the app object directly — uvicorn string-import doesn't work in
    # frozen PyInstaller bundles because importlib can't resolve dotted paths.
    from ada_pdf.api.app import app  # noqa: PLC0415
    import uvicorn
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=PORT,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
