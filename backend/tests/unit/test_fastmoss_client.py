"""FastMoss MCP 客户端解析测试（不联网）。样例结构来自 2026-09-29 实测，数值为虚构。"""

import json

import httpx
import pytest

from tk_workspace.integrations.fastmoss.mcp_client import (
    FastMossAuthError,
    FastMossInsufficientCredits,
    FastMossMCPClient,
    parse_tool_result,
)


def _tool_body(payload: dict, charge: dict | None = None) -> dict:
    meta = {"request_id": "req-1", "tool_name": "creator_search"}
    if charge is not None:
        meta["charge"] = charge
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"_meta": meta, "content": [{"type": "text", "text": json.dumps(payload)}]},
    }


def test_parse_charge_and_payload():
    body = _tool_body(
        {"list": [], "total": 0}, {"charged": False, "credit_cost": 0, "remaining_credits": 100}
    )
    r = parse_tool_result("creator_search", body)
    assert r.data == {"list": [], "total": 0}
    assert r.charge and r.charge.charged is False and r.charge.remaining_credits == 100
    assert r.request_id == "req-1"


def test_insufficient_credits_error():
    with pytest.raises(FastMossInsufficientCredits):
        parse_tool_result(
            "creator_search",
            {"jsonrpc": "2.0", "id": 1, "error": {"code": 402, "message": "Payment Required"}},
        )


def test_client_session_and_sse_response():
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer k-test"
        assert "k-test" not in str(request.url)
        payload = json.loads(request.content)
        calls.append({"method": payload.get("method"), "sid": request.headers.get("Mcp-Session-Id")})
        if payload.get("method") == "initialize":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 0, "result": {}}, headers={"mcp-session-id": "s-1"}
            )
        if payload.get("method") == "notifications/initialized":
            return httpx.Response(202)
        body = _tool_body(
            {"balance": {"available_credits": 90}},
            {"charged": False, "credit_cost": 0, "remaining_credits": 90},
        )
        return httpx.Response(
            200,
            text="event: message\ndata: " + json.dumps(body) + "\n\n",
            headers={"content-type": "text/event-stream"},
        )

    with FastMossMCPClient(
        "https://example.invalid/mcp", "k-test", transport=httpx.MockTransport(handler)
    ) as c:
        r = c.call_tool("credit_usage_summary")
    assert r.data["balance"]["available_credits"] == 90
    assert [x["method"] for x in calls] == ["initialize", "notifications/initialized", "tools/call"]
    assert calls[-1]["sid"] == "s-1"


def test_client_rejects_missing_key():
    with pytest.raises(FastMossAuthError):
        FastMossMCPClient("https://example.invalid/mcp", "")
