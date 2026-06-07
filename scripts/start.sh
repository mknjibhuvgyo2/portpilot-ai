#!/usr/bin/env bash
# AI Port Hub - one-shot dev/prod start (bash)
# Usage:  ./scripts/start.sh          build frontend + run backend serving it
#         ./scripts/start.sh dev      run backend (reload) + vite dev server
set -e
root="$(cd "$(dirname "$0")/.." && pwd)"

# --- Backend venv + deps ---
cd "$root/backend"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/python -m pip install -q --upgrade pip
./.venv/bin/python -m pip install -q -r requirements.txt

if [ "$1" = "dev" ]; then
  echo "Starting backend (:8000, reload) in background and vite dev (:5173)..."
  ./.venv/bin/python -m uvicorn app.main:app --reload --port 8000 &
  cd "$root/frontend"
  [ -d node_modules ] || npm install
  npm run dev
else
  echo "Building frontend..."
  cd "$root/frontend"
  [ -d node_modules ] || npm install
  npm run build
  echo "Starting backend on http://localhost:8000 ..."
  cd "$root/backend"
  exec ./.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
fi
