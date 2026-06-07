#!/usr/bin/env bash
# ADA PDF Converter — one-click launcher (macOS / Linux)
# First run: installs everything automatically (~10-15 min).
# Subsequent runs: starts in ~5 seconds.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$REPO_DIR/backend"
FRONTEND="$REPO_DIR/frontend/ADA_PDF_Converter.html"
VENV="$BACKEND/.venv-simple"
PORT="${ADA_PDF_PORT:-8765}"

echo ""
echo " ┌─────────────────────────────────────────┐"
echo " │       ADA PDF Converter                 │"
echo " └─────────────────────────────────────────┘"
echo ""

# ── Step 1: check Python ──────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo " [ERROR] Python 3.11+ is required but not installed."
    echo " Install from https://python.org  (macOS: brew install python@3.11)"
    exit 1
fi

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo " Python $PY_VER found."

# ── Step 2: install if first run ─────────────────────────────────────────
if [ ! -f "$VENV/bin/python3" ]; then
    echo ""
    echo " First run — setting up environment (10-15 min, once only)..."
    echo ""
    bash "$BACKEND/scripts/install.sh"
fi

# ── Step 3: start backend ────────────────────────────────────────────────
PY="$VENV/bin/python3"
ENV_FILE="$BACKEND/.env.simple"

[ -f "$ENV_FILE" ] && set -a && source "$ENV_FILE" && set +a

export SIMPLE_MODE=true
export DATABASE_URL="sqlite+aiosqlite:///./ada_pdf.db"
cd "$BACKEND"

echo ""
echo " Backend starting on http://127.0.0.1:$PORT"
echo " Press Ctrl+C to stop."
echo ""

# ── Step 4: open frontend HTML after 2 seconds ──────────────────────────
(sleep 2 && (open "$FRONTEND" 2>/dev/null || xdg-open "$FRONTEND" 2>/dev/null || true)) &

"$PY" -m uvicorn ada_pdf.api.app:app \
    --host 127.0.0.1 \
    --port "$PORT" \
    --log-level warning
