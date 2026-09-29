#!/bin/bash
# 创建数据库角色与数据库。Docker 首次初始化时自动执行；CI 中也可手动执行（设置 PGHOST/PGPASSWORD）。
# 角色划分（见 docs/architecture/02-data-model.md 4.2）：
#   app_owner      表所有者，只用于迁移
#   app_runtime    API / Worker 运行角色：非所有者、NOBYPASSRLS
#   app_dispatcher Outbox 分发角色：只能读写 outbox_events
set -euo pipefail
DB_NAME="${DB_NAME:-tk_workspace}"
PSQL=(psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER:-postgres}" --dbname postgres)

"${PSQL[@]}" \
  -v owner_pw="$APP_OWNER_PASSWORD" \
  -v runtime_pw="$APP_RUNTIME_PASSWORD" \
  -v dispatcher_pw="$APP_DISPATCHER_PASSWORD" <<'SQL'
CREATE ROLE app_owner LOGIN NOSUPERUSER NOCREATEROLE NOBYPASSRLS PASSWORD :'owner_pw';
CREATE ROLE app_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD :'runtime_pw';
CREATE ROLE app_dispatcher LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD :'dispatcher_pw';
SQL

for db in "$DB_NAME" "${DB_NAME}_test"; do
  "${PSQL[@]}" -c "CREATE DATABASE \"$db\" OWNER app_owner;"
  psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER:-postgres}" --dbname "$db" <<'SQL'
REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO app_owner;
GRANT USAGE ON SCHEMA public TO app_runtime, app_dispatcher;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS citext;
SQL
done
echo "roles and databases ready"
