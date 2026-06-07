@echo off
setlocal enabledelayedexpansion
title ADA PDF Converter

set REPO_DIR=%~dp0
set REPO_DIR=%REPO_DIR:~0,-1%
set BACKEND=%REPO_DIR%\backend
set FRONTEND=%REPO_DIR%\frontend\ADA_PDF_Converter.html
set VENV=%BACKEND%\.venv-simple
set PORT=8765

echo.
echo  +-----------------------------------------+
echo  ^|       ADA PDF Converter                 ^|
echo  +-----------------------------------------+
echo.

:: ── Check Python ──────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python 3.11+ is required but not installed.
    echo  Download from https://python.org
    echo  Make sure to tick "Add Python to PATH" during install.
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo  Python !PYVER! found.

:: ── Install if first run ──────────────────────────────────────────────
if not exist "%VENV%\Scripts\python.exe" (
    echo.
    echo  First run - setting up environment (10-15 min, once only)...
    echo.
    call "%BACKEND%\scripts\install.bat"
    if errorlevel 1 (
        echo  [ERROR] Setup failed. Check the output above.
        pause
        exit /b 1
    )
)

:: ── Start backend ────────────────────────────────────────────────────
set PY=%VENV%\Scripts\python.exe
set ENV_FILE=%BACKEND%\.env.simple

if exist "%ENV_FILE%" (
    for /f "usebackq tokens=1,* delims==" %%a in ("%ENV_FILE%") do (
        if not "%%a"=="" if not "%%a:~0,1%"=="#" set "%%a=%%b"
    )
)

set SIMPLE_MODE=true
set DATABASE_URL=sqlite+aiosqlite:///./ada_pdf.db
cd /d "%BACKEND%"

echo.
echo  Backend starting on http://127.0.0.1:%PORT%
echo  Opening frontend/ADA_PDF_Converter.html in browser...
echo  Close this window to stop the server.
echo.

:: Open frontend HTML after 2 seconds
start "" /b cmd /c "timeout /t 2 /nobreak >nul && start """" ""%FRONTEND%"""

:: Start server
"%PY%" -m uvicorn ada_pdf.api.app:app ^
    --host 127.0.0.1 ^
    --port %PORT% ^
    --log-level warning

if errorlevel 1 pause
