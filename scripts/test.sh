#!/bin/bash
# 运行全部检查：后端 lint + 测试，前端类型检查 + 构建。
set -euo pipefail
cd "$(dirname "$0")/.."
(cd backend && uv run ruff check . && uv run pytest -q)
(cd apps/web && pnpm typecheck && pnpm build)
