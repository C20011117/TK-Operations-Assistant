"""运行配置（桌面版）。

桌面应用没有 .env：数据目录、启动令牌等由外壳通过环境变量传入；
FastMoss / 大模型的密钥保存在 Windows 凭据管理器，非密钥配置保存在数据库 app_settings 表。
这里只放“进程启动时就要确定”的配置。
"""

import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME = "TKWorkspace"
APP_VERSION = "0.2.0"
KEYRING_SERVICE = "TKWorkspace"


def default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / APP_NAME


def bundle_root() -> Path:
    """打包后为 PyInstaller 解包目录；源码运行时为 backend/ 目录。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TKWS_", extra="ignore")

    app_env: Literal["production", "development", "test"] = "production"
    data_dir: Path = Field(default_factory=default_data_dir)

    # 外壳每次启动生成，不落盘；为空时只允许开发模式（使用固定开发令牌）
    launch_token: str = ""
    parent_pid: int | None = None
    allowed_origins: list[str] = ["http://tauri.localhost", "https://tauri.localhost", "tauri://localhost"]

    fastmoss_mcp_url: str = "https://mcp.fastmoss.com/mcp"
    job_workers: int = Field(default=2, ge=1, le=8)
    job_lease_seconds: int = Field(default=300, ge=10)
    job_poll_seconds: float = Field(default=0.5, gt=0)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "data" / "app.db"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path.as_posix()}"

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    def ensure_dirs(self) -> None:
        for d in (self.db_path.parent, self.files_dir, self.logs_dir, self.backups_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
