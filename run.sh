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

# ── 3. Ensure TA-Lib C library is installed ─────────────────────
if ! ldconfig -p 2>/dev/null | grep -q libta-lib; then
  echo "[…] TA-Lib C library not found. Installing..."
  echo "    (requires sudo — you may be prompted for your password)"
  sudo apt-get update -qq
  sudo apt-get install -y -qq libta-lib0-dev ta-lib 2>/dev/null || {
    # If not in apt, build from source
    echo "[…] Not in apt — building TA-Lib from source..."
    cd /tmp
    wget -q https://github.com/ta-lib/ta-lib/releases/download/v0.6.4/ta-lib-0.6.4-src.tar.gz
    tar -xzf ta-lib-0.6.4-src.tar.gz
    cd ta-lib-0.6.4
    ./configure --prefix=/usr/local
    make -j"$(nproc)"
    sudo make install
    sudo ldconfig
    cd "$SCRIPT_DIR"
    rm -rf /tmp/ta-lib-0.6.4 /tmp/ta-lib-0.6.4-src.tar.gz
  }
  echo "[✓] TA-Lib C library installed."
else
  echo "[✓] TA-Lib C library found."
fi

# ── 4. Install / update dependencies ────────────────────────────
if [ "requirements.txt" -nt "venv/.deps_installed" ]; then
  echo "[…] Installing dependencies (this may take a minute)..."
  pip install --upgrade pip --quiet
  pip install -r requirements.txt --quiet
  touch venv/.deps_installed
  echo "[✓] Dependencies installed."
else
  echo "[✓] Dependencies up to date."
fi

# ── 5. Pre-pull embedding model for RAG ─────────────────────────
# Read embedding model + Ollama URL from llm_config.json if set
EMBED_MODEL="nomic-embed-text:latest"
OLLAMA_URL="http://localhost:11434"
LLM_CONFIG="app/user_config/llm_config.json"
if [ -f "$LLM_CONFIG" ]; then
  CUSTOM_EMBED=$(python3 -c "
import json
try:
    d = json.load(open('$LLM_CONFIG'))
    print(d.get('embedding_model', ''))
except: pass
" 2>/dev/null)
  if [ -n "$CUSTOM_EMBED" ]; then
    EMBED_MODEL="$CUSTOM_EMBED"
  fi
  CUSTOM_URL=$(python3 -c "
import json
try:
    d = json.load(open('$LLM_CONFIG'))
    print(d.get('ollama_url', ''))
except: pass
" 2>/dev/null)
  if [ -n "$CUSTOM_URL" ]; then
    OLLAMA_URL="$CUSTOM_URL"
  fi
fi
echo "[…] Ensuring embedding model '$EMBED_MODEL' is available on $OLLAMA_URL..."
# Use the HTTP API (not CLI) — the ollama binary may not be on PATH in VS Code
# NOTE: This entire block is non-fatal. If Ollama is unreachable (exit 7),
# the bot will retry the model pull at startup. Don't crash the launcher.
set +e  # Temporarily disable exit-on-error
if curl -sf --connect-timeout 5 "$OLLAMA_URL/api/show" -d "{\"name\": \"$EMBED_MODEL\"}" >/dev/null 2>&1; then
  echo "[✓] Embedding model '$EMBED_MODEL' already pulled."
else
  echo "[…] Pulling embedding model '$EMBED_MODEL' (first time only)..."
  curl -sf --connect-timeout 10 "$OLLAMA_URL/api/pull" -d "{\"name\": \"$EMBED_MODEL\", \"stream\": false}" >/dev/null 2>&1
  if [ $? -eq 0 ]; then
    echo "[✓] Embedding model pulled."
  else
    echo "[⚠] Could not reach Ollama at $OLLAMA_URL — server will retry at startup."
  fi
fi
set -e  # Re-enable exit-on-error

# ── 6. Ensure data directories exist ────────────────────────────
mkdir -p data/cache data/reports logs

# ── 7. Build Frontend UI ─────────────────────────────────────────
if [ -d "ui" ]; then
  echo "[…] Checking Node-Based Pipeline UI build..."
  if [ ! -d "ui/dist" ]; then
    echo "[…] Building UI for the first time..."
    (cd ui && npm install --silent && npm run build --silent)
    echo "[✓] UI built."
  fi
fi

# ── 8. Launch the server ─────────────────────────────────────────
echo
echo "========================================="
echo "  Starting server on http://localhost:8000"
echo "  API docs:  http://localhost:8000/docs"
echo "========================================="
echo
export PYTHONIOENCODING=utf-8
python server.py
