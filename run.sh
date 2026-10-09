#!/usr/bin/env bash
# One-command demo: build the UI (if needed) and serve API + UI at http://127.0.0.1:8000
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d frontend/dist ] || [ "${REBUILD:-0}" = "1" ]; then
  (cd frontend && npm install --no-fund --no-audit && npm run build)
fi
python3 -c "import ortools, fastapi" 2>/dev/null || pip install -e "engine[api]"

echo "VAYU-SARTHI: open http://127.0.0.1:8000"
cd engine && exec python3 -m uvicorn sarthi.api:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}"
