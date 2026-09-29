# TK 多站点达人开发与内容运营工作台

以企业自己的产品、预算和合作经验为依据，结合用户授权的 FastMoss 数据，帮助同一名 BD 独立完成找人、寄样、拍摄指导、视频回收反馈、持续产出、达人分类和长期维护。

**形态：Windows 桌面应用**。双击安装、双击运行，单机使用，数据只保存在本机，不需要 Docker、数据库服务或浏览器。

当前状态：**桌面版工程地基（M0-D）已完成，业务功能尚未实现**。纵向切片以欧洲站点为先。未作性能或业务效果承诺。

- [桌面版架构方案](docs/桌面版架构方案.md)：当前生效的技术方案（Tauri 外壳 + 本机 Python 后端 + SQLite）。
- [纵向切片方案](docs/纵向切片方案.md)：实施范围、欧洲 11 站、里程碑与 FastMoss 预算。
- [最终产品规格](docs/product/final-spec.md)：需求基线。
- [原服务器版架构](docs/architecture/README.md)：业务流程、数据模型、AI 流程仍作参考；部署与多租户部分已被桌面版方案取代。

## 使用

运行安装包 `TK达人工作台_<版本>_x64-setup.exe`（安装到当前用户，无需管理员权限），从开始菜单或桌面打开。首次使用在“设置”页填写 FastMoss 与大模型的 API Key（保存在 Windows 凭据管理器）。

数据位置：`%LOCALAPPDATA%\TKWorkspace\`（`data\app.db` 数据库、`logs\` 日志）。卸载程序不会删除这个目录。

## 开发

需要：Python 3.12 + [uv](https://docs.astral.sh/uv/)、Node 24 + pnpm 10、Rust（stable，MSVC 工具链）、WebView2（Windows 10/11 自带）。

```bash
# 方式一：完整桌面窗口（外壳自动用 uv 启动后端、启动 Vite）
cd apps/web && pnpm install
cd ../desktop && pnpm install && pnpm dev

# 方式二：只在浏览器里调界面
cd backend && uv run python -m tk_workspace.desktop --dev    # 固定端口 8765、开发令牌
cd apps/web && pnpm dev                                       # 打开 http://localhost:5173
```

打包安装包（PowerShell）：`.\scripts\build-desktop.ps1`，产物在 `apps\desktop\src-tauri\target\release\bundle\nsis\`。

常用命令：`./scripts/test.sh`（全部检查）、`./scripts/gen-api.sh`（后端接口变化后重新生成前端类型）、`cd backend && uv run pytest -m live`（真实调用 FastMoss，仅免费接口，需先在设置页保存 Key）。

| 目录 | 内容 |
|---|---|
| `apps/desktop/` | Tauri 2 外壳（Rust）：启动/看护后端、单实例、安装包配置 |
| `apps/web/` | React + Vite 界面，接口类型由 OpenAPI 生成 |
| `backend/` | FastAPI 本机后端、SQLite + Alembic 迁移、应用内任务执行器、PyInstaller 打包配置、测试 |
| `docs/` | 产品规格、架构设计、纵向切片方案 |

文档修订日期：2026-09-29。
