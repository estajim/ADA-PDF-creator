@echo off
setlocal
title ADA PDF Converter

set SCRIPT_DIR=%~dp0
set ROOT_DIR=%SCRIPT_DIR%..
cd /d "%ROOT_DIR%"

set VENV_DIR=%ROOT_DIR%\.venv-simple
set PY=%VENV_DIR%\Scripts\python.exe
set ENV_FILE=%ROOT_DIR%\.env.simple

:: ── Sanity checks ────────────────────────────────────────────────────────
if not exist "%PY%" (
    echo  Setup has not been run yet.
    echo  Please run install.bat first.
    pause
    exit /b 1
)

:: ── Load environment variables from .env.simple ──────────────────────────
if exist "%ENV_FILE%" (
    for /f "usebackq tokens=1,* delims==" %%a in ("%ENV_FILE%") do (
        if not "%%a"=="" if not "%%a:~0,1%"=="#" set "%%a=%%b"
    )
)

set SIMPLE_MODE=true
set DATABASE_URL=sqlite+aiosqlite:///./ada_pdf.db

:: ── Find a free port (default 8765) ─────────────────────────────────────
set PORT=8765

:: ── Start server ─────────────────────────────────────────────────────────
echo.
echo  Starting ADA PDF Converter on http://127.0.0.1:%PORT%
echo  Opening browser in a moment...
echo  Close this window to stop the server.
echo.

:: Open browser after 2 seconds in background
start "" /b cmd /c "timeout /t 2 /nobreak >nul && start http://127.0.0.1:%PORT%"

:: Run uvicorn
"%PY%" -m uvicorn ada_pdf.api.app:app ^
    --host 127.0.0.1 ^
    --port %PORT% ^
    --log-level warning

if errorlevel 1 (
    echo.
    echo  [ERROR] Server failed to start. Check the output above.
    pause
)
