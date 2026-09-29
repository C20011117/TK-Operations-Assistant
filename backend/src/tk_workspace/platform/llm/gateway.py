"""大模型网关（OpenAI 兼容接口）。业务代码只通过这里调用模型，不直接构造客户端。

- base_url / 模型名来自“设置”页面，API Key 来自 Windows 凭据管理器。
- 结构化输出方式可切换（json_schema / function_calling / json_mode），返回内容一律再用 Pydantic 校验。
"""

import time
from dataclasses import dataclass
from typing import Literal

from tk_workspace.modules.settings.service import LLMConfig, get_llm_config

CheckStatus = Literal["ok", "not_configured", "error"]


@dataclass(frozen=True)
class LLMCheck:
    status: CheckStatus
    model: str | None = None
    latency_ms: int | None = None
    detail: str = ""
    data_region: str = ""


def chat_model(purpose: Literal["matching", "brief"] = "matching", cfg: LLMConfig | None = None):
    from langchain_openai import ChatOpenAI

    c = cfg or get_llm_config()
    model = c.model_matching if purpose == "matching" else (c.model_brief or c.model_matching)
    return ChatOpenAI(
        base_url=c.base_url,
        api_key=c.api_key,
        model=model,
        timeout=c.timeout_seconds,
        max_retries=1,
        temperature=0,
    )


def check(cfg: LLMConfig | None = None) -> LLMCheck:
    """连通检查：发送一条极短的请求，不发送任何业务数据。"""
    c = cfg or get_llm_config()
    if not c.configured:
        return LLMCheck(status="not_configured", detail="请在设置中填写接口地址、API Key 和模型名")
    started = time.monotonic()
    try:
        reply = chat_model("matching", c).invoke("Reply with the single word OK.")
        text = str(getattr(reply, "content", ""))[:20]
    except Exception as exc:  # noqa: BLE001
        return LLMCheck(
            status="error", model=c.model_matching, detail=f"{type(exc).__name__}: {str(exc)[:200]}"
        )
    return LLMCheck(
        status="ok",
        model=c.model_matching,
        latency_ms=int((time.monotonic() - started) * 1000),
        detail=f"reply={text!r}",
        data_region=c.data_region,
    )
