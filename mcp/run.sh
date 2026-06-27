#!/usr/bin/env bash
# Launcher for the volttron-fuxa MCP server. Resolves the venv + server.py
# relative to this script so the only absolute path Claude Code needs is to
# run.sh itself (in ../.mcp.json).
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$DIR/.venv/bin/python" "$DIR/server.py" "$@"
