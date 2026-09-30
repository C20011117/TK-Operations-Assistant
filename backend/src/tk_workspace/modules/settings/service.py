"""应用设置：非密钥配置存 app_settings 表，密钥存 Windows 凭据管理器。"""

import json
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import text

from tk_workspace.platform import secrets
from tk_workspace.platform.db.engine import tx
from tk_workspace.platform.db.types import utcnow_iso

StructuredMode = Literal["json_schema", "function_calling", "json_mode"]


class LLMConfig(BaseModel):
    base_url: str = ""
    model_matching: str = ""
    model_brief: str = ""
    structured_mode: StructuredMode = "json_schema"
    timeout_seconds: int = Field(default=180, ge=5, le=600)
    data_region: str = ""
    api_key: str = Field(default="", exclude=True)

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model_matching)


def _get(key: str) -> dict[str, Any] | None:
    with tx() as s:
        row = s.execute(text("SELECT value FROM app_settings WHERE key = :k"), {"k": key}).first()
    return json.loads(row[0]) if row else None


def _put(key: str, value: dict[str, Any]) -> None:
    with tx() as s:
        s.execute(
            text(
                """INSERT INTO app_settings (key, value, updated_at) VALUES (:k, :v, :t)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at"""
            ),
            {"k": key, "v": json.dumps(value, ensure_ascii=False), "t": utcnow_iso()},
        )


def get_llm_config() -> LLMConfig:
    data = _get("llm") or {}
    return LLMConfig(**data, api_key=secrets.get_secret(secrets.LLM_API_KEY))


def save_llm_config(cfg: LLMConfig, api_key: str | None) -> LLMConfig:
    """api_key 为 None 表示不修改；空字符串表示删除。"""
    _put("llm", cfg.model_dump())
    if api_key is not None:
        if api_key.strip():
            secrets.set_secret(secrets.LLM_API_KEY, api_key.strip())
        else:
            secrets.delete_secret(secrets.LLM_API_KEY)
    return get_llm_config()


def get_fastmoss_key() -> str:
    return secrets.get_secret(secrets.FASTMOSS_API_KEY)


def set_fastmoss_key(api_key: str) -> None:
    if api_key.strip():
        secrets.set_secret(secrets.FASTMOSS_API_KEY, api_key.strip())
    else:
        secrets.delete_secret(secrets.FASTMOSS_API_KEY)
