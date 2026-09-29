"""部署配置。只从环境变量 / .env 读取；启动时校验必需项，日志只报告缺失的变量名。"""

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(_REPO_ROOT / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["development", "test", "staging", "production"] = "development"
    public_origin: str = "http://localhost:5173"

    db_host: str = "localhost"
    db_port: int = 55432
    db_name: str = "tk_workspace"
    app_owner_password: SecretStr = SecretStr("")
    app_runtime_password: SecretStr = SecretStr("")
    app_dispatcher_password: SecretStr = SecretStr("")
    postgres_superuser_password: SecretStr = SecretStr("")

    redis_url: str = "redis://localhost:56379/0"

    session_ttl_hours: int = 12

    llm_base_url: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_model_matching: str = ""
    llm_model_brief: str = ""
    llm_structured_mode: Literal["json_schema", "function_calling", "json_mode"] = "json_schema"
    llm_timeout_seconds: int = 60
    llm_data_region: str = ""
    llm_provider_dpa_ref: str = ""

    fastmoss_mcp_url: str = "https://mcp.fastmoss.com/mcp"
    fastmoss_mcp_api_key: SecretStr = SecretStr("")

    seed_dev_password: SecretStr = SecretStr("")

    job_lease_seconds: int = Field(default=300, ge=10)

    def _dsn(self, driver: str, user: str, password: SecretStr, db: str | None = None) -> str:
        pw = quote(password.get_secret_value(), safe="")
        return f"postgresql+{driver}://{user}:{pw}@{self.db_host}:{self.db_port}/{db or self.db_name}"

    # API 使用 asyncpg（兼容 Windows 默认事件循环）；Worker / 迁移使用同步 psycopg。
    @property
    def runtime_async_dsn(self) -> str:
        return self._dsn("asyncpg", "app_runtime", self.app_runtime_password)

    @property
    def runtime_sync_dsn(self) -> str:
        return self._dsn("psycopg", "app_runtime", self.app_runtime_password)

    @property
    def dispatcher_sync_dsn(self) -> str:
        return self._dsn("psycopg", "app_dispatcher", self.app_dispatcher_password)

    @property
    def owner_sync_dsn(self) -> str:
        return self._dsn("psycopg", "app_owner", self.app_owner_password)

    @property
    def superuser_sync_dsn(self) -> str:
        """仅供开发种子数据和测试夹具使用，生产环境禁止。"""
        if self.app_env in ("staging", "production"):
            raise RuntimeError("superuser DSN is not available outside development/test")
        return self._dsn("psycopg", "postgres", self.postgres_superuser_password)

    @property
    def is_dev_like(self) -> bool:
        return self.app_env in ("development", "test")

    def missing_required(self) -> list[str]:
        missing = []
        for name in ("app_runtime_password",):
            if not getattr(self, name).get_secret_value():
                missing.append(name.upper())
        return missing


@lru_cache
def get_settings() -> Settings:
    return Settings()
