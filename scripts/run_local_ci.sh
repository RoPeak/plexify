#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if command -v python >/dev/null 2>&1 && python -c 'import pytest, typer, rich, requests, guessit, rapidfuzz' >/dev/null 2>&1; then
  PYTHON="$(command -v python)"
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
else
  PYTHON="$(command -v python3)"
fi
exec "$PYTHON" "$ROOT/scripts/local_ci.py" "${1:?expected fast or push}"
