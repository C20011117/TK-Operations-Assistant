"""设置接口：大模型与 FastMoss 配置、外部服务连通检查。密钥只写不读，接口只返回末 4 位。"""

import asyncio
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

from tk_workspace.integrations.fastmoss import operations as fm_ops
from tk_workspace.integrations.fastmoss.mcp_client import FastMossError
from tk_workspace.modules.settings import service
from tk_workspace.platform import secrets
from tk_workspace.platform.llm import gateway

router = APIRouter(prefix="/settings", tags=["settings"])


class LLMSettingsView(BaseModel):
    base_url: str
    model_matching: str
    model_brief: str
    structured_mode: service.StructuredMode
    timeout_seconds: int
    data_region: str
    api_key_hint: str | None
    configured: bool


class FastMossSettingsView(BaseModel):
    api_key_hint: str | None
    configured: bool


class SettingsView(BaseModel):
    llm: LLMSettingsView
    fastmoss: FastMossSettingsView


class LLMSettingsUpdate(BaseModel):
    base_url: str = Field(default="", max_length=500)
    model_matching: str = Field(default="", max_length=200)
    model_brief: str = Field(default="", max_length=200)
    structured_mode: service.StructuredMode = "json_schema"
    timeout_seconds: int = Field(default=60, ge=5, le=600)
    data_region: str = Field(default="", max_length=100)
    # None：不修改已保存的 Key；""：删除
    api_key: str | None = Field(default=None, max_length=500)

    @field_validator("base_url")
    @classmethod
    def _url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if v and not v.startswith(("https://", "http://")):
            raise ValueError("接口地址必须以 https:// 或 http:// 开头")
        return v


class FastMossSettingsUpdate(BaseModel):
    api_key: str = Field(max_length=500)


def _view() -> SettingsView:
    llm = service.get_llm_config()
    fm_key = service.get_fastmoss_key()
    return SettingsView(
        llm=LLMSettingsView(
            **llm.model_dump(), api_key_hint=secrets.hint(llm.api_key), configured=llm.configured
        ),
        fastmoss=FastMossSettingsView(api_key_hint=secrets.hint(fm_key), configured=bool(fm_key)),
    )


@router.get("", response_model=SettingsView, summary="当前设置（不含密钥明文）")
def get_settings_view() -> SettingsView:
    return _view()


@router.put("/llm", response_model=SettingsView, summary="保存大模型设置")
def put_llm(body: LLMSettingsUpdate) -> SettingsView:
    cfg = service.LLMConfig(**body.model_dump(exclude={"api_key"}))
    service.save_llm_config(cfg, body.api_key)
    return _view()


@router.put("/fastmoss", response_model=SettingsView, summary="保存 FastMoss API Key（空字符串表示删除）")
def put_fastmoss(body: FastMossSettingsUpdate) -> SettingsView:
    service.set_fastmoss_key(body.api_key)
    return _view()


class ExternalCheck(BaseModel):
    status: Literal["ok", "not_configured", "error"]
    detail: str = ""
    model: str | None = None
    latency_ms: int | None = None
    available_credits: int | None = None
    credit_cost: int | None = None


class ExternalChecks(BaseModel):
    llm: ExternalCheck
    fastmoss: ExternalCheck


def _fastmoss_check() -> ExternalCheck:
    if not service.get_fastmoss_key():
        return ExternalCheck(status="not_configured", detail="请在设置中填写 FastMoss API Key")
    try:
        res = fm_ops.execute("account.credits")
    except FastMossError as exc:
        return ExternalCheck(status="error", detail=f"{exc.code}: {str(exc)[:200]}")
    except Exception as exc:  # noqa: BLE001
        return ExternalCheck(status="error", detail=f"{type(exc).__name__}: {str(exc)[:200]}")
    balance = (res.data or {}).get("balance", {}) if isinstance(res.data, dict) else {}
    return ExternalCheck(
        status="ok",
        detail="credit_usage_summary（免费接口）",
        available_credits=balance.get("available_credits"),
        credit_cost=res.charge.credit_cost if res.charge else None,
    )


@router.post("/checks", response_model=ExternalChecks, summary="外部服务连通检查")
async def external_checks() -> ExternalChecks:
    llm_res, fm_res = await asyncio.gather(
        asyncio.to_thread(gateway.check), asyncio.to_thread(_fastmoss_check)
    )
    return ExternalChecks(
        llm=ExternalCheck(
            status=llm_res.status, detail=llm_res.detail, model=llm_res.model, latency_ms=llm_res.latency_ms
        ),
        fastmoss=fm_res,
    )
