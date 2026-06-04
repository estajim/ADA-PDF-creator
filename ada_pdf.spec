# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for ADA PDF Converter — Windows one-click desktop app.

Build:
    pyinstaller ada_pdf.spec

Output: dist/ADA_PDF_Converter/  (--onedir, fastest startup)
"""

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH)

# ── Hidden imports PyInstaller misses ─────────────────────────────────────
hidden = [
    # FastAPI / Starlette internals
    "uvicorn.logging", "uvicorn.loops", "uvicorn.loops.auto",
    "uvicorn.protocols", "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto", "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto", "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    # SQLAlchemy dialects
    "sqlalchemy.dialects.sqlite",
    "aiosqlite",
    # pikepdf / QPDF
    "pikepdf._core",
    # WeasyPrint
    "weasyprint", "weasyprint.text.ffi",
    # fontTools
    "fontTools.ttLib", "fontTools.subset",
    # Pydantic
    "pydantic.deprecated.class_validators",
    # Email validator (Pydantic dep)
    "email_validator",
    # Our own modules
    *collect_submodules("ada_pdf"),
]

# ── Data files (templates, static, fonts, models) ─────────────────────────
datas = [
    # Web UI
    (str(ROOT / "ada_pdf" / "static"),    "ada_pdf/static"),
    (str(ROOT / "ada_pdf" / "templates"), "ada_pdf/templates"),
]

# Collect WeasyPrint's bundled CSS/fonts
datas += collect_data_files("weasyprint")

# fontTools data
datas += collect_data_files("fontTools")

# ── Analysis ───────────────────────────────────────────────────────────────
a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude heavy packages not needed in SIMPLE_MODE
        "celery", "redis", "psycopg2", "asyncpg",
        # Exclude test frameworks
        "pytest", "hypothesis",
        # Exclude Jupyter
        "IPython", "jupyter",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

import platform as _platform
_IS_MAC = _platform.system() == "Darwin"
_ICNS   = ROOT / "build" / "ada_pdf_icon.icns"
_ICON   = str(_ICNS) if (_IS_MAC and _ICNS.exists()) else None

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ADA_PDF_Converter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=_ICON,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="ADA_PDF_Converter",
)

# ── macOS .app bundle ─────────────────────────────────────────────────────
if _IS_MAC:
    app = BUNDLE(
        coll,
        name="ADA PDF Converter.app",
        icon=_ICON,
        bundle_identifier="com.ada-pdf-creator.app",
        version="1.0.0",
        info_plist={
            "CFBundleName":            "ADA PDF Converter",
            "CFBundleDisplayName":     "ADA PDF Converter",
            "CFBundleVersion":         "1.0.0",
            "CFBundleShortVersionString": "1.0.0",
            "NSPrincipalClass":        "NSApplication",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion":  "13.0",
            "NSAppleScriptEnabled":    False,
            "LSUIElement":             False,  # show in Dock
        },
    )
