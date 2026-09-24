#!/usr/bin/env bash
set -euo pipefail

ROOT="${CLAUDE_PLUGIN_ROOT:?CLAUDE_PLUGIN_ROOT 未设置}"
PYTHON="${PYTHON:-python3}"
exec "$PYTHON" "$ROOT/scripts/bootstrap.py"
