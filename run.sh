#!/bin/bash
# CliDeck launcher. First run will create .venv and install deps.
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    echo "[clideck] creating venv..."
    python3 -m venv .venv
fi

source .venv/bin/activate

if ! python -c "import fastapi" 2>/dev/null; then
    echo "[clideck] installing deps..."
    pip install -q -e .
fi

PORT="${CLIDECK_PORT:-${AGENT_CONSOLE_PORT:-7878}}"
echo "[clideck] listening on http://127.0.0.1:${PORT}"
exec uvicorn app:app --host 127.0.0.1 --port "$PORT" --reload
