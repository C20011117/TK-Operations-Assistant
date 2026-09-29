#!/bin/bash
# 首次使用：根据 .env.example 生成 .env，并为本地数据库 / 对象存储 / 种子账号生成随机密码。
# 已存在 .env 时不会覆盖。
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then
  echo ".env 已存在，未修改"
  exit 0
fi
rand() { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c "${1:-24}"; }
cp .env.example .env
set_var() { sed -i "s|^$1=.*|$1=$2|" .env; }
set_var POSTGRES_SUPERUSER_PASSWORD "$(rand)"
set_var APP_OWNER_PASSWORD "$(rand)"
set_var APP_RUNTIME_PASSWORD "$(rand)"
set_var APP_DISPATCHER_PASSWORD "$(rand)"
set_var OBJECT_STORAGE_ACCESS_KEY "tkws$(rand 8)"
set_var OBJECT_STORAGE_SECRET_KEY "$(rand 32)"
set_var SEED_DEV_PASSWORD "Dev-$(rand 12)"
echo "已生成 .env。请补充 LLM_* 与 FASTMOSS_MCP_API_KEY。"
