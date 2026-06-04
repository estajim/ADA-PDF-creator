#!/usr/bin/env bash
# ADA PDF Converter — First-time setup for macOS / Linux
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$ROOT_DIR/.venv-simple"
ENV_FILE="$ROOT_DIR/.env.simple"
VERAPDF_DIR="$ROOT_DIR/verapdf"

echo ""
echo " ====================================================="
echo "  ADA PDF Converter — First-time Setup"
echo " ====================================================="
echo ""

# ── Python check ─────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo " [ERROR] python3 not found."
    echo " Install Python 3.11+ from https://python.org or via:"
    echo "   brew install python@3.11   (macOS)"
    echo "   sudo apt install python3.11  (Ubuntu/Debian)"
    exit 1
fi

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJ=$(echo "$PY_VER" | cut -d. -f1)
PY_MIN=$(echo "$PY_VER" | cut -d. -f2)
if [ "$PY_MAJ" -lt 3 ] || { [ "$PY_MAJ" -eq 3 ] && [ "$PY_MIN" -lt 11 ]; }; then
    echo " [ERROR] Python $PY_VER found, but 3.11+ is required."
    exit 1
fi
echo " [OK] Python $PY_VER"

# ── Virtual environment ───────────────────────────────────────────────────
if [ -f "$VENV_DIR/bin/python3" ]; then
    echo " [OK] Virtual environment already exists."
else
    echo " Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
    echo " [OK] Virtual environment created."
fi

PY="$VENV_DIR/bin/python3"
PIP="$VENV_DIR/bin/pip"

# ── Dependencies ──────────────────────────────────────────────────────────
echo ""
echo " Installing Python packages (10-15 min on first run)..."
"$PIP" install --upgrade pip --quiet
"$PIP" install -r "$ROOT_DIR/requirements.txt" \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    --quiet
echo " [OK] Packages installed."

# ── veraPDF ───────────────────────────────────────────────────────────────
if [ -f "$VERAPDF_DIR/verapdf" ]; then
    echo " [OK] veraPDF already installed."
else
    echo " Downloading veraPDF..."
    TMP_ZIP=$(mktemp /tmp/verapdf_XXXX.zip)
    curl -fsSL \
        "https://software.verapdf.org/releases/1.26/verapdf-greenfield-1.26.2-installer.zip" \
        -o "$TMP_ZIP"
    TMP_DIR=$(mktemp -d)
    unzip -q "$TMP_ZIP" -d "$TMP_DIR"
    INST_DIR=$(find "$TMP_DIR" -name "verapdf-greenfield-*" -type d | head -1)
    if [ -n "$INST_DIR" ] && command -v java &>/dev/null; then
        java -jar "$INST_DIR/verapdf-installer.jar" \
            -installs "$VERAPDF_DIR" -console >/dev/null 2>&1 || true
    fi
    rm -rf "$TMP_ZIP" "$TMP_DIR"
    if [ -f "$VERAPDF_DIR/verapdf" ]; then
        chmod +x "$VERAPDF_DIR/verapdf"
        echo " [OK] veraPDF installed."
    else
        echo " [WARN] veraPDF not installed — PDF/UA validation will be skipped."
    fi
fi

# ── ML models ─────────────────────────────────────────────────────────────
echo ""
echo " Downloading AI models (~2 GB, one-time)..."
"$PY" "$ROOT_DIR/scripts/download_models.py" || echo " [WARN] Model download had issues."
echo " [OK] Models ready."

# ── .env.simple ───────────────────────────────────────────────────────────
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" <<EOF
SIMPLE_MODE=true
DATABASE_URL=sqlite+aiosqlite:///./ada_pdf.db
STORAGE_ROOT=$ROOT_DIR/storage
VERAPDF_CLI_PATH=$VERAPDF_DIR/verapdf
ANTHROPIC_API_KEY=
CORS_ALLOWED_ORIGINS=*
RATE_LIMIT_PER_MINUTE=600
MAX_FILE_SIZE_MB=200
EOF
    echo " [OK] Configuration written to .env.simple"
fi

# ── Done ──────────────────────────────────────────────────────────────────
echo ""
echo " ====================================================="
echo "  Setup complete!"
echo "  Run:  bash scripts/start.sh"
echo "  Or double-click start.command (macOS)"
echo " ====================================================="
echo ""
