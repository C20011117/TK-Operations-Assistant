"""FastMoss MCP 最小客户端（Streamable HTTP, JSON-RPC 2.0）。

只实现连接器需要的部分：initialize → notifications/initialized → tools/list / tools/call。
- Key 只放在 Authorization 请求头，绝不拼进 URL，也不写入日志。
- 结果在 content[0].text 里是 JSON 字符串；计费信息在 result._meta.charge。
- 服务器可能返回 application/json 或 text/event-stream，两种都解析。
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger(__name__)
PROTOCOL_VERSION = "2025-03-26"


class FastMossError(Exception):
    code = "provider_error"


class FastMossAuthError(FastMossError):
    code = "unauthorized"


class FastMossInsufficientCredits(FastMossError):
    code = "insufficient_credits"

    def __init__(self, message: str, recharge_url: str | None = None) -> None:
        super().__init__(message)
        self.recharge_url = recharge_url


class FastMossRateLimited(FastMossError):
    code = "rate_limited"


class FastMossToolError(FastMossError):
    code = "tool_error"


@dataclass(frozen=True)
class Charge:
    charged: bool
    credit_cost: int
    remaining_credits: int | None


@dataclass(frozen=True)
class ToolResult:
    tool: str
    data: Any
    charge: Charge | None
    request_id: str | None
    raw_meta: dict[str, Any] = field(default_factory=dict)


def _parse_body(resp: httpx.Response) -> dict[str, Any]:
    ctype = resp.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        last = None
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload:
                    last = json.loads(payload)
        if last is None:
            raise FastMossError("empty event stream")
        return last
    return resp.json()


def parse_tool_result(tool: str, body: dict[str, Any]) -> ToolResult:
    if "error" in body:
        err = body["error"] or {}
        msg = str(err.get("message", err))[:300]
        code = err.get("code")
        if code == 402 or "insufficient" in msg.lower() or "402" in msg:
            raise FastMossInsufficientCredits(msg, (err.get("data") or {}).get("recharge_url"))
        raise FastMossToolError(msg)
    result = body.get("result") or {}
    meta = result.get("_meta") or {}
    charge_raw = meta.get("charge")
    charge = (
        Charge(
            charged=bool(charge_raw.get("charged")),
            credit_cost=int(charge_raw.get("credit_cost") or 0),
            remaining_credits=charge_raw.get("remaining_credits"),
        )
        if isinstance(charge_raw, dict)
        else None
    )
    content = result.get("content") or []
    text = content[0].get("text", "") if content and isinstance(content[0], dict) else ""
    if result.get("isError"):
        if "insufficient" in text.lower() or "402" in text:
            raise FastMossInsufficientCredits(text[:300])
        raise FastMossToolError(text[:300] or "tool returned isError")
    if "structuredContent" in result:
        data = result["structuredContent"]
    else:
        try:
            data = json.loads(text) if text else None
        except json.JSONDecodeError:
            data = {"_text": text}
    return ToolResult(tool=tool, data=data, charge=charge, request_id=meta.get("request_id"), raw_meta=meta)


class FastMossMCPClient:
    def __init__(
        self, url: str, api_key: str, *, timeout: float = 60.0, transport: httpx.BaseTransport | None = None
    ) -> None:
        if not api_key:
            raise FastMossAuthError("FastMoss MCP API key is not configured")
        self._url = url
        self._http = httpx.Client(
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        )
        self._session_id: str | None = None
        self._next_id = 1

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "FastMossMCPClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _post(self, payload: dict[str, Any]) -> httpx.Response:
        headers = {"Mcp-Session-Id": self._session_id} if self._session_id else {}
        resp = self._http.post(self._url, json=payload, headers=headers)
        if resp.status_code == 401:
            raise FastMossAuthError("FastMoss rejected the API key")
        if resp.status_code == 402:
            raise FastMossInsufficientCredits("FastMoss credits are insufficient")
        if resp.status_code == 429:
            raise FastMossRateLimited("FastMoss rate limit reached")
        if resp.status_code == 404 and self._session_id:
            # 会话过期：清掉后由调用方重新 initialize
            self._session_id = None
            raise FastMossError("session expired")
        resp.raise_for_status()
        return resp

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        req_id = self._next_id
        self._next_id += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            payload["params"] = params
        return _parse_body(self._post(payload))

    def _ensure_session(self) -> None:
        if not self._session_id:
            resp = self._http.post(
                self._url,
                json={
                    "jsonrpc": "2.0",
                    "id": 0,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "tk-workspace", "version": "0.1.0"},
                    },
                },
            )
            if resp.status_code == 401:
                raise FastMossAuthError("FastMoss rejected the API key")
            resp.raise_for_status()
            _parse_body(resp)
            self._session_id = resp.headers.get("mcp-session-id")
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def list_tools(self) -> list[dict[str, Any]]:
        self._ensure_session()
        return self._rpc("tools/list").get("result", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        self._ensure_session()
        try:
            body = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        except FastMossError as exc:
            if str(exc) != "session expired":
                raise
            self._ensure_session()
            body = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        return parse_tool_result(name, body)
