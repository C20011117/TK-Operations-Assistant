#!/bin/bash
# 后端接口变化后运行：导出 OpenAPI → 生成前端 TypeScript 类型。
set -euo pipefail
cd "$(dirname "$0")/.."
(cd backend && uv run python -m tk_workspace.api.export_openapi ../apps/web/openapi.json)
(cd apps/web && pnpm gen:api)
