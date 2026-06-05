#!/usr/bin/env bash
# ADA PDF Converter — launcher for macOS / Linux
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$ROOT_DIR/.venv-simple"
ENV_FILE="$ROOT_DIR/.env.simple"
PY="$VENV_DIR/bin/python3"
PORT="${ADA_PDF_PORT:-8765}"
URL="http://127.0.0.1:$PORT"

if [ ! -f "$PY" ]; then
    echo "[ERROR] Setup not complete. Run:  bash scripts/install.sh"
    exit 1
fi

# Load .env.simple
if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

export SIMPLE_MODE=true
export DATABASE_URL="sqlite+aiosqlite:///./ada_pdf.db"
cd "$ROOT_DIR"

echo ""
echo " ADA PDF Converter → $URL"
echo " Press Ctrl+C to stop."
echo ""

# Open browser after 2s
(sleep 2 && (open "$URL" 2>/dev/null || xdg-open "$URL" 2>/dev/null || true)) &

"$PY" -m uvicorn ada_pdf.api.app:app \
    --host 127.0.0.1 \
    --port "$PORT" \
    --log-level warning
