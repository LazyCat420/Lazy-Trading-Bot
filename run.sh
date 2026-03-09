#!/usr/bin/env bash
set -e

echo "========================================="
echo "  Lazy Trading Bot — WSL Launcher"
echo "========================================="
echo

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── 1. Ensure Python 3.12+ is available ─────────────────────────
PYTHON=""
for candidate in python3.12 python3; do
  if command -v "$candidate" &>/dev/null; then
    PYTHON="$candidate"
    break
  fi
done

if [ -z "$PYTHON" ]; then
  echo "ERROR: Python 3.12+ is required but not found."
  echo "Install it with:  sudo apt install -y python3.12 python3.12-venv python3.12-dev"
  exit 1
fi

PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$("$PYTHON" -c "import sys; print(sys.version_info.major)")
PY_MINOR=$("$PYTHON" -c "import sys; print(sys.version_info.minor)")

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 12 ]; }; then
  echo "ERROR: Python >= 3.12 required (found $PY_VER)."
  echo "Install it with:  sudo apt install -y python3.12 python3.12-venv python3.12-dev"
  exit 1
fi

echo "[✓] Python $PY_VER found ($PYTHON)"

# ── 2. Create venv if missing ────────────────────────────────────
if [ ! -f "venv/bin/activate" ]; then
  echo "[…] Creating virtual environment..."
  "$PYTHON" -m venv venv
  echo "[✓] Virtual environment created."
fi

source venv/bin/activate
echo "[✓] Virtual environment activated."

# ── 3. Install / update dependencies ────────────────────────────
if [ "requirements.txt" -nt "venv/.deps_installed" ]; then
  echo "[…] Installing dependencies (this may take a minute)..."
  pip install --upgrade pip --quiet
  pip install -r requirements.txt --quiet
  touch venv/.deps_installed
  echo "[✓] Dependencies installed."
else
  echo "[✓] Dependencies up to date."
fi

# ── 4. Ensure data directories exist ────────────────────────────
mkdir -p data/cache data/reports logs

# ── 5. Launch the server ─────────────────────────────────────────
echo
echo "========================================="
echo "  Starting server on http://localhost:8000"
echo "  API docs:  http://localhost:8000/docs"
echo "========================================="
echo
python server.py
