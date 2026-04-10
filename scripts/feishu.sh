#!/usr/bin/env bash
# Thin wrapper so `./scripts/feishu.sh ...` works without remembering uv syntax.
set -euo pipefail
SKILL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SKILL_ROOT"
exec uv run python -m feishu_super "$@"
