@echo off
setlocal enabledelayedexpansion
title ADA PDF Converter — Setup

echo.
echo  =====================================================
echo   ADA PDF Converter — First-time Setup
echo  =====================================================
echo.

:: ── Check Python ────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found.
    echo  Please install Python 3.11 or newer from https://python.org
    echo  Make sure to tick "Add Python to PATH" during install.
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
for /f "tokens=1,2 delims=." %%a in ("!PYVER!") do (
    set PYMAJ=%%a
    set PYMIN=%%b
)
if !PYMAJ! LSS 3 goto :bad_python
if !PYMAJ! EQU 3 if !PYMIN! LSS 11 goto :bad_python
goto :python_ok

:bad_python
echo  [ERROR] Python !PYVER! found, but 3.11+ is required.
echo  Download from https://python.org
pause
exit /b 1

:python_ok
echo  [OK] Python !PYVER! found.

:: ── Set paths ────────────────────────────────────────────────────────────
set SCRIPT_DIR=%~dp0
set ROOT_DIR=%SCRIPT_DIR%..
cd /d "%ROOT_DIR%"

set VENV_DIR=%ROOT_DIR%\.venv-simple

:: ── Create venv ──────────────────────────────────────────────────────────
if exist "%VENV_DIR%\Scripts\python.exe" (
    echo  [OK] Virtual environment already exists — skipping creation.
) else (
    echo  Creating virtual environment...
    python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo  [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo  [OK] Virtual environment created.
)

set PY=%VENV_DIR%\Scripts\python.exe
set PIP=%VENV_DIR%\Scripts\pip.exe

:: ── Install dependencies ─────────────────────────────────────────────────
echo.
echo  Installing Python packages (this may take 10-15 minutes on first run)...
echo  Tip: you can minimise this window and come back later.
echo.

"%PIP%" install --upgrade pip --quiet
"%PIP%" install -r "%ROOT_DIR%\requirements.txt" ^
    --extra-index-url https://download.pytorch.org/whl/cpu ^
    --quiet
if errorlevel 1 (
    echo  [ERROR] Package installation failed.
    echo  Check your internet connection and try running install.bat again.
    pause
    exit /b 1
)
echo  [OK] Packages installed.

:: ── Download veraPDF ────────────────────────────────────────────────────
set VERAPDF_DIR=%ROOT_DIR%\verapdf
if exist "%VERAPDF_DIR%\verapdf.bat" (
    echo  [OK] veraPDF already downloaded.
) else (
    echo  Downloading veraPDF validator...
    powershell -Command ^
        "Invoke-WebRequest -Uri 'https://software.verapdf.org/releases/1.26/verapdf-greenfield-1.26.2-installer.zip' -OutFile '%TEMP%\verapdf.zip'"
    if errorlevel 1 (
        echo  [WARN] veraPDF download failed — validation will be skipped.
    ) else (
        powershell -Command "Expand-Archive -Path '%TEMP%\verapdf.zip' -DestinationPath '%TEMP%\verapdf_inst' -Force"
        for /d %%d in ("%TEMP%\verapdf_inst\verapdf-greenfield-*") do (
            "%VENV_DIR%\Scripts\java" -jar "%%d\verapdf-installer.jar" ^
                -installs "%VERAPDF_DIR%" -console >nul 2>&1
        )
        if not exist "%VERAPDF_DIR%\verapdf.bat" (
            echo  [WARN] veraPDF install failed — validation will be skipped.
        ) else (
            echo  [OK] veraPDF installed.
        )
    )
)

:: ── Download ML models ───────────────────────────────────────────────────
echo.
echo  Downloading AI models (Surya OCR + BLIP alt-text, ~2 GB total)...
echo  This is a one-time download. Subsequent starts are instant.
echo.
"%PY%" "%ROOT_DIR%\scripts\download_models.py"
if errorlevel 1 (
    echo  [WARN] Model download had issues — OCR and alt-text may be limited.
)
echo  [OK] Models ready.

:: ── Write .env.simple ────────────────────────────────────────────────────
set ENV_FILE=%ROOT_DIR%\.env.simple
if not exist "%ENV_FILE%" (
    (
        echo SIMPLE_MODE=true
        echo DATABASE_URL=sqlite+aiosqlite:///./ada_pdf.db
        echo STORAGE_ROOT=%ROOT_DIR%\storage
        echo VERAPDF_CLI_PATH=%VERAPDF_DIR%\verapdf.bat
        echo ANTHROPIC_API_KEY=
        echo CORS_ALLOWED_ORIGINS=*
        echo RATE_LIMIT_PER_MINUTE=600
        echo MAX_FILE_SIZE_MB=200
    ) > "%ENV_FILE%"
    echo  [OK] Configuration file created.
)

:: ── Done ─────────────────────────────────────────────────────────────────
echo.
echo  =====================================================
echo   Setup complete!
echo   Double-click  start.bat  to launch the converter.
echo  =====================================================
echo.
pause
