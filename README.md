# TK 多站点达人开发与内容运营工作台

以企业自己的产品、预算和合作经验为依据，结合用户授权的 FastMoss 数据，帮助同一名 BD 独立完成找人、寄样、拍摄指导、视频回收反馈、持续产出、达人分类和长期维护。

当前仓库状态：**完整架构设计 + 纵向切片实施中（M0 工程地基已完成，业务功能尚未实现）**。纵向切片以欧洲站点为先，范围和里程碑见 [纵向切片方案](docs/纵向切片方案.md)。未作性能或业务效果承诺。

- [纵向切片方案](docs/纵向切片方案.md)：当前实施范围、欧洲 11 站、里程碑 M0–M5 与 FastMoss 预算。
- [架构总入口](docs/architecture/README.md)：技术选型、系统图、数据模型、AI 流程、接口、前端、安全、部署和验收。
- [最终产品规格](docs/product/final-spec.md)：本次设计的需求基线。
- [系统与工程结构](docs/architecture/01-system-design.md)：从部署组件到代码组织的说明。
- [完整验收与需求追踪](docs/architecture/06-verification-and-traceability.md)：判定应用能否交付的证据要求。

核心技术方案为 React + TypeScript 前端、FastAPI 模块化单体、独立异步 Worker、PostgreSQL + pgvector、Redis、对象存储，以及在 Worker 内运行的 LangChain / LangGraph。详细约束以架构文档为准。

## 本地开发

需要：Docker Desktop、Python 3.12 + [uv](https://docs.astral.sh/uv/)、Node 24 + pnpm 10、Git Bash（Windows）。

```bash
./scripts/init-env.sh      # 生成 .env（随机本地密码），再手动填 LLM_* 与 FASTMOSS_MCP_API_KEY
./scripts/dev-up.sh        # Docker 起 PostgreSQL/Redis/MinIO → 迁移 → 种子账号 → Worker 与分发器
cd backend && uv run uvicorn tk_workspace.api.main:app --reload --port 8000
cd apps/web && pnpm install && pnpm dev    # 打开 http://localhost:5173
```

开发账号（密码为 `.env` 中的 `SEED_DEV_PASSWORD`）：

| 账号 | 企业 | 角色 |
|---|---|---|
| admin@demo.local | 演示企业 | 管理员 |
| bd.a@demo.local / bd.b@demo.local | 演示企业 | BD（彼此数据隔离） |
| bd.c@other.local | 另一家企业 | BD（跨企业隔离验证） |

常用命令：`./scripts/test.sh`（全部检查）、`cd backend && uv run python ../scripts/m0_acceptance.py`（M0 端到端验收，需服务均在运行）、`./scripts/gen-api.sh`（后端接口变化后重新生成前端类型）、`cd backend && uv run pytest -m live`（真实调用 FastMoss，仅免费接口）。

本地端口：PostgreSQL 55432、Redis 56379、MinIO 59000/59001、API 8000、前端 5173。

| 目录 | 内容 |
|---|---|
| `backend/` | FastAPI 模块化单体、Celery Worker、Outbox 分发器、Alembic 迁移、测试 |
| `apps/web/` | React + Vite 前端，接口类型由 OpenAPI 生成 |
| `infra/compose/` | 本地 Docker Compose 与数据库角色初始化 |
| `docs/` | 产品规格、架构设计、纵向切片方案 |

文档修订日期：2026-09-29。架构文档中的路径结构、接口、指标目标及 JSON 为拟实现设计；已实现部分以代码和测试为准。
