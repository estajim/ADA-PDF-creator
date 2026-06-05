"""First-run ML environment installer.

Downloads uv (a fast Python package manager), creates a dedicated ML
virtual environment at ~/.ada-pdf-creator/ml-venv, installs all heavy
ML packages (PyTorch, Surya OCR, BLIP alt-text, docling, WeasyPrint),
then downloads the model weights.

The installer streams progress as plain text lines — callers (the SSE
endpoint) iterate over _run_setup() and yield each line to the browser.
"""
from __future__ import annotations

import asyncio
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import AsyncIterator

# ── Paths ─────────────────────────────────────────────────────────────────
_ADA_DIR   = Path.home() / ".ada-pdf-creator"
ML_VENV    = _ADA_DIR / "ml-venv"
READY_FILE = _ADA_DIR / ".ml_ready"
_UV_DIR    = _ADA_DIR / "uv"

_IS_WIN    = platform.system() == "Windows"
_IS_MAC    = platform.system() == "Darwin"

# ── ML packages to install (order matters — torch first) ──────────────────
_ML_PACKAGES: list[tuple[str, list[str]]] = [
    ("PyTorch (CPU)",         ["torch==2.5.1",
                               "--extra-index-url",
                               "https://download.pytorch.org/whl/cpu"]),
    ("Surya OCR engine",      ["surya-ocr==0.8.1"]),
    ("AI models (BLIP/BERT)", ["transformers==4.47.1"]),
    ("Layout analysis",       ["docling==2.15.1"]),
    ("PDF generator",         ["weasyprint==62.3"]),
    ("Language detection",    ["lingua-language-detector==2.0.2"]),
]

# ── uv binary URLs ─────────────────────────────────────────────────────────
_UV_VERSION = "0.5.24"
_UV_URLS: dict[str, str] = {
    "Windows": (
        f"https://github.com/astral-sh/uv/releases/download/{_UV_VERSION}"
        f"/uv-x86_64-pc-windows-msvc.zip"
    ),
    "Darwin": (
        f"https://github.com/astral-sh/uv/releases/download/{_UV_VERSION}"
        f"/uv-aarch64-apple-darwin.tar.gz"
    ),
    "Linux": (
        f"https://github.com/astral-sh/uv/releases/download/{_UV_VERSION}"
        f"/uv-x86_64-unknown-linux-gnu.tar.gz"
    ),
}


# ── Public API ─────────────────────────────────────────────────────────────

def is_ml_ready() -> bool:
    """True when the ML venv exists and is fully set up."""
    return READY_FILE.exists()


def get_ml_site_packages() -> Path | None:
    """Return the site-packages path inside the ML venv, or None."""
    if not ML_VENV.exists():
        return None
    if _IS_WIN:
        sp = ML_VENV / "Lib" / "site-packages"
    else:
        # e.g.  ml-venv/lib/python3.11/site-packages
        lib = ML_VENV / "lib"
        if lib.exists():
            for child in sorted(lib.iterdir()):
                sp = child / "site-packages"
                if sp.exists():
                    return sp
        return None
    return sp if sp.exists() else None


async def run_setup() -> AsyncIterator[str]:
    """Async generator — yields progress lines consumed by the SSE endpoint."""
    _ADA_DIR.mkdir(parents=True, exist_ok=True)

    yield "=== ADA PDF Converter — First-time Setup ===\n"
    yield f"Installing to: {ML_VENV}\n\n"

    # Step 1 — download uv
    yield "── Step 1/4: Downloading uv package manager…\n"
    uv = await _ensure_uv()
    if uv is None:
        yield "ERROR: Could not download uv. Check your internet connection.\n"
        return
    yield f"✓ uv ready at {uv}\n\n"

    # Step 2 — install Python 3.11 via uv
    yield "── Step 2/4: Installing Python 3.11…\n"
    async for line in _stream(uv, "python", "install", "3.11"):
        yield line
    yield "✓ Python 3.11 ready\n\n"

    # Step 3 — create ML venv
    yield "── Step 3/4: Creating ML virtual environment…\n"
    if ML_VENV.exists():
        shutil.rmtree(ML_VENV)
    async for line in _stream(uv, "venv", str(ML_VENV), "--python", "3.11"):
        yield line
    yield f"✓ Venv created at {ML_VENV}\n\n"

    # Step 4 — install ML packages
    yield "── Step 4/4: Installing ML packages (this takes 5-10 minutes)…\n"
    venv_python = _venv_python()
    for label, pkgs in _ML_PACKAGES:
        yield f"   Installing {label}…\n"
        cmd = [str(uv), "pip", "install",
               "--python", str(venv_python)] + pkgs
        async for line in _stream_cmd(cmd):
            # Only surface meaningful lines (filter pip noise)
            stripped = line.strip()
            if stripped and not stripped.startswith("Downloading http"):
                yield f"   {stripped}\n"
        yield f"   ✓ {label}\n"
    yield "\n"

    # Mark complete
    READY_FILE.write_text("ready")
    yield "✓ Setup complete!\n"
    yield "SETUP_DONE\n"


# ── Internal helpers ───────────────────────────────────────────────────────

async def _ensure_uv() -> Path | None:
    """Download uv if not already present, return path to binary."""
    uv_bin = _UV_DIR / ("uv.exe" if _IS_WIN else "uv")
    if uv_bin.exists():
        return uv_bin

    url = _UV_URLS.get(platform.system())
    if not url:
        return None

    _UV_DIR.mkdir(parents=True, exist_ok=True)
    archive = _UV_DIR / Path(url).name
    try:
        def _dl():
            urllib.request.urlretrieve(url, archive)
        await asyncio.to_thread(_dl)
    except Exception as exc:
        return None

    # Extract
    try:
        if url.endswith(".zip"):
            import zipfile
            with zipfile.ZipFile(archive) as zf:
                for member in zf.namelist():
                    if member.endswith("uv.exe") or member.endswith("/uv"):
                        zf.extract(member, _UV_DIR)
                        extracted = _UV_DIR / member
                        extracted.rename(uv_bin)
                        break
        else:
            import tarfile
            with tarfile.open(archive) as tf:
                for m in tf.getmembers():
                    if m.name.endswith("/uv") or m.name == "uv":
                        m.name = "uv"
                        tf.extract(m, _UV_DIR)
                        break
        if not _IS_WIN:
            uv_bin.chmod(0o755)
    except Exception:
        return None
    finally:
        archive.unlink(missing_ok=True)

    return uv_bin if uv_bin.exists() else None


def _venv_python() -> Path:
    if _IS_WIN:
        return ML_VENV / "Scripts" / "python.exe"
    return ML_VENV / "bin" / "python"


async def _stream(*args: str) -> AsyncIterator[str]:
    """Stream a uv subprocess line by line."""
    async for line in _stream_cmd([str(a) for a in args]):
        yield line


async def _stream_cmd(cmd: list[str]) -> AsyncIterator[str]:
    """Run cmd as async subprocess and yield stdout+stderr lines."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        yield line.decode(errors="replace")
    await proc.wait()
