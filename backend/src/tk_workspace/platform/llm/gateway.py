"""大模型网关（OpenAI 兼容接口）。业务代码只通过这里调用模型，不直接构造客户端。

- base_url / api_key / 模型名全部来自配置；企业不能提交任意 URL 让服务器携密钥访问。
- 结构化输出方式可切换（json_schema / function_calling / json_mode），返回内容一律再用 Pydantic 校验。
"""

import time
from dataclasses import dataclass
from typing import Literal

from tk_workspace.config import Settings, get_settings

CheckStatus = Literal["ok", "not_configured", "error"]


@dataclass(frozen=True)
class LLMCheck:
    status: CheckStatus
    model: str | None = None
    latency_ms: int | None = None
    detail: str = ""
    data_region: str = ""


def is_configured(settings: Settings | None = None) -> bool:
    s = settings or get_settings()
    return bool(s.llm_base_url and s.llm_api_key.get_secret_value() and s.llm_model_matching)


def chat_model(purpose: Literal["matching", "brief"] = "matching", settings: Settings | None = None):
    from langchain_openai import ChatOpenAI

    s = settings or get_settings()
    model = s.llm_model_matching if purpose == "matching" else (s.llm_model_brief or s.llm_model_matching)
    return ChatOpenAI(
        base_url=s.llm_base_url,
        api_key=s.llm_api_key.get_secret_value(),
        model=model,
        timeout=s.llm_timeout_seconds,
        max_retries=1,
        temperature=0,
    )


def check(settings: Settings | None = None) -> LLMCheck:
    """连通检查：发送一条极短的请求。不发送任何企业数据。"""
    s = settings or get_settings()
    if not is_configured(s):
        return LLMCheck(
            status="not_configured", detail="未配置 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL_MATCHING"
        )
    started = time.monotonic()
    try:
        reply = chat_model("matching", s).invoke("Reply with the single word OK.")
        text = str(getattr(reply, "content", ""))[:20]
    except Exception as exc:  # noqa: BLE001
        return LLMCheck(
            status="error", model=s.llm_model_matching, detail=f"{type(exc).__name__}: {str(exc)[:200]}"
        )
    return LLMCheck(
        status="ok",
        model=s.llm_model_matching,
        latency_ms=int((time.monotonic() - started) * 1000),
        detail=f"reply={text!r}",
        data_region=s.llm_data_region,
    )
