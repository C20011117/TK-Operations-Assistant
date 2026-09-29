#!/bin/bash
# 启动 Docker 基础服务 → 迁移数据库 → 写入种子账号 → 构建并启动 Worker / 分发器。
set -euo pipefail
cd "$(dirname "$0")/.."
COMPOSE=(docker compose -f infra/compose/compose.yaml --env-file .env)
"${COMPOSE[@]}" up -d --wait postgres redis minio
"${COMPOSE[@]}" up minio-init
(cd backend && uv sync && uv run alembic upgrade head && uv run python -m tk_workspace.scripts.seed_dev)
"${COMPOSE[@]}" up -d --build worker dispatcher
echo
echo "基础服务已就绪。接下来在两个终端分别运行："
echo "  cd backend && uv run uvicorn tk_workspace.api.main:app --reload --port 8000"
echo "  cd apps/web && pnpm dev"
