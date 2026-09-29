"""FastMoss 调用：幂等复用、限流、调用记录与费用流水。

- 同一运行内，参数完全相同的请求只真正调用一次；已成功（含空结果）的调用直接复用规范化快照。
- 上次调用在进行中被中断（状态 pending）时，供应方是否已执行、是否扣费都未知，不自动重发，状态改为 unknown。
- 额度不足 / Key 无效不重试；限流最多等待重试 2 次；超时记为 unknown，不自动重发。
- 模型不参与这里的任何决定；密钥只在客户端请求头中，不写入记录。
"""

import json
import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from pydantic import BaseModel
from sqlalchemy import text

from tk_workspace.integrations.fastmoss import creator_search as cs
from tk_workspace.integrations.fastmoss.mcp_client import (
    FastMossAuthError,
    FastMossInsufficientCredits,
    FastMossRateLimited,
    ToolResult,
)
from tk_workspace.integrations.fastmoss.operations import OPERATIONS
from tk_workspace.platform.db.engine import tx
from tk_workspace.platform.db.types import utcnow_iso
from tk_workspace.platform.jobs.service import request_hash

log = logging.getLogger(__name__)


class ToolClient(Protocol):
    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult: ...
    def close(self) -> None: ...


_client_factory: Callable[[], ToolClient] | None = None


def set_client_factory(factory: Callable[[], ToolClient] | None) -> None:
    """仅供测试注入假客户端（FixtureAdapter）；生产环境拒绝。"""
    from tk_workspace.config import get_settings

    if factory is not None and get_settings().app_env == "production":
        raise RuntimeError("fixture provider is not allowed in production")
    global _client_factory
    _client_factory = factory


def new_client() -> ToolClient:
    if _client_factory is not None:
        return _client_factory()
    from tk_workspace.integrations.fastmoss.operations import client_from_settings

    return client_from_settings()


def transport_name() -> str:
    return "fixture" if _client_factory is not None else "mcp"


class RateLimiter:
    """进程内按最小间隔限流（试用 / Basic 每分钟 40 次 → 至少间隔 1.5 秒）。"""

    def __init__(self, per_minute: int = 40) -> None:
        self.interval = 60.0 / per_minute
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self.interval
        if delay > 0:
            time.sleep(delay)


limiter = RateLimiter()
RATE_LIMIT_RETRIES = 2
RATE_LIMIT_BACKOFF_SECONDS = 5.0


@dataclass
class CallOutcome:
    call_id: str
    status: str  # succeeded | empty | failed | insufficient_credits | rate_limited | unauthorized | unknown
    records: list[dict[str, Any]] = field(default_factory=list)
    total: int | None = None
    credits: int = 0
    remaining_credits: int | None = None
    reused: bool = False
    message: str = ""


def _load_snapshot(s, call_id: str) -> list[dict[str, Any]]:
    row = s.execute(
        text("SELECT records FROM provider_snapshots WHERE provider_call_id=:c"), {"c": call_id}
    ).first()
    return json.loads(row.records) if row else []


def normalize_result(op, data: Any, validated: BaseModel) -> tuple[list[dict[str, Any]], int | None]:
    if op.normalize is not None:
        return op.normalize(data, validated)
    raw_list, total = cs.extract_list(data)
    return [cs.normalize_record(r) for r in raw_list], total


def _invoke(client: ToolClient, tool: str, args: dict[str, Any]) -> tuple[ToolResult | None, str, str]:
    """调用一次（含限流重试）。返回 (结果, 状态, 说明)。"""
    status, message = "failed", ""
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        limiter.wait()
        try:
            return client.call_tool(tool, args), "ok", ""
        except FastMossRateLimited as e:
            status, message = "rate_limited", str(e)
            if attempt < RATE_LIMIT_RETRIES:
                time.sleep(RATE_LIMIT_BACKOFF_SECONDS * (attempt + 1))
        except FastMossInsufficientCredits as e:
            return None, "insufficient_credits", str(e)
        except FastMossAuthError as e:
            return None, "unauthorized", str(e)
        except httpx.TimeoutException:
            return None, "unknown", "请求超时，无法确认 FastMoss 是否已执行和扣费"
        except Exception as e:  # noqa: BLE001  其他错误记录为失败，由调用方决定是否继续
            return None, "failed", f"{type(e).__name__}: {str(e)[:200]}"
    return None, status, message


def call_direct(op_name: str, params: BaseModel, client: ToolClient | None = None) -> CallOutcome:
    """不属于任何运行的调用（类目识别、竞品推荐）：不缓存，扣费写入 usage_ledger（run_id 为空）。"""
    op = OPERATIONS[op_name]
    validated = op.params_model.model_validate(params.model_dump())
    owns = client is None
    c = client or new_client()
    try:
        result, status, message = _invoke(c, op.tool, validated.model_dump(exclude_none=True))
    finally:
        if owns:
            c.close()
    if result is None:
        return CallOutcome(call_id="", status=status, message=message)
    records, total = normalize_result(op, result.data, validated)
    charge = result.charge
    credits = charge.credit_cost if charge and charge.charged else 0
    if charge is None and records:
        credits = op.credit_cost_estimate(validated)
    if credits:
        with tx() as s:
            s.execute(
                text(
                    """INSERT INTO usage_ledger (id, run_id, source, operation, credits, created_at)
                       VALUES (:id, NULL, 'fastmoss', :op, :cr, :n)"""
                ),
                {"id": str(uuid.uuid4()), "op": op_name, "cr": credits, "n": utcnow_iso()},
            )
    return CallOutcome(
        call_id="",
        status="succeeded" if records else "empty",
        records=records,
        total=total,
        credits=credits,
        remaining_credits=charge.remaining_credits if charge else None,
    )


def call(run_id: str, op_name: str, params: BaseModel, client: ToolClient) -> CallOutcome:
    op = OPERATIONS[op_name]
    validated = op.params_model.model_validate(params.model_dump())
    args = validated.model_dump(exclude_none=True)
    rhash = request_hash({"op": op_name, "args": args})
    now = utcnow_iso()

    with tx() as s:
        row = s.execute(
            text(
                """SELECT id, status, total, credit_cost, charged, remaining_credits FROM provider_calls
                   WHERE run_id=:r AND request_hash=:h"""
            ),
            {"r": run_id, "h": rhash},
        ).first()
        if row and row.status in ("succeeded", "empty"):
            return CallOutcome(
                call_id=row.id,
                status=row.status,
                records=_load_snapshot(s, row.id),
                total=row.total,
                remaining_credits=row.remaining_credits,
                reused=True,
            )
        if row and row.status in ("pending", "unknown"):
            s.execute(
                text("UPDATE provider_calls SET status='unknown', finished_at=:n WHERE id=:id"),
                {"n": now, "id": row.id},
            )
            return CallOutcome(
                call_id=row.id,
                status="unknown",
                message="上次调用在执行中被中断，无法确认 FastMoss 是否已执行和扣费，未自动重发",
            )
        if row:  # failed / rate_limited 等可以再试：复用同一逻辑调用记录
            call_id = row.id
            s.execute(
                text("UPDATE provider_calls SET status='pending', error=NULL, finished_at=NULL WHERE id=:id"),
                {"id": call_id},
            )
        else:
            call_id = str(uuid.uuid4())
            s.execute(
                text(
                    """INSERT INTO provider_calls (id, run_id, operation, tool, transport, request_hash, params,
                                                  status, created_at)
                       VALUES (:id, :r, :op, :tool, :tr, :h, :p, 'pending', :n)"""
                ),
                {
                    "id": call_id,
                    "r": run_id,
                    "op": op_name,
                    "tool": op.tool,
                    "tr": transport_name(),
                    "h": rhash,
                    "p": json.dumps(args, ensure_ascii=False),
                    "n": now,
                },
            )

    result, status, message = _invoke(client, op.tool, args)

    out = CallOutcome(call_id=call_id, status=status, message=message)
    fin = utcnow_iso()
    if result is not None:
        records, total = normalize_result(op, result.data, validated)
        charge = result.charge
        charged = charge.charged if charge else None
        credits = charge.credit_cost if charge and charge.charged else 0
        if charge is None and records:
            credits = op.credit_cost_estimate(validated)  # 没有计费信息时按预计扣费保守记账
        out = CallOutcome(
            call_id=call_id,
            status="succeeded" if records else "empty",
            records=records,
            total=total,
            credits=credits,
            remaining_credits=charge.remaining_credits if charge else None,
        )
        with tx() as s:
            s.execute(
                text(
                    """UPDATE provider_calls SET status=:st, provider_request_id=:rid, charged=:ch,
                              credit_cost=:cc, remaining_credits=:rem, result_count=:n, total=:t, finished_at=:f
                       WHERE id=:id"""
                ),
                {
                    "st": out.status,
                    "rid": result.request_id,
                    "ch": None if charged is None else int(charged),
                    "cc": credits,
                    "rem": out.remaining_credits,
                    "n": len(records),
                    "t": total,
                    "f": fin,
                    "id": call_id,
                },
            )
            s.execute(
                text(
                    """INSERT INTO provider_snapshots (id, provider_call_id, schema_version, records, created_at)
                       VALUES (:id, :c, :v, :r, :n)
                       ON CONFLICT(provider_call_id) DO UPDATE SET records=excluded.records"""
                ),
                {
                    "id": str(uuid.uuid4()),
                    "c": call_id,
                    "v": cs.SCHEMA_VERSION,
                    "r": json.dumps(records, ensure_ascii=False),
                    "n": fin,
                },
            )
            if credits:
                s.execute(
                    text(
                        """INSERT INTO usage_ledger (id, run_id, source, operation, provider_call_id, credits,
                                                    created_at)
                           VALUES (:id, :r, 'fastmoss', :op, :c, :cr, :n)"""
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "r": run_id,
                        "op": op_name,
                        "c": call_id,
                        "cr": credits,
                        "n": fin,
                    },
                )
                s.execute(
                    text("UPDATE matching_runs SET credits_used = credits_used + :cr WHERE id=:r"),
                    {"cr": credits, "r": run_id},
                )
    else:
        with tx() as s:
            s.execute(
                text("UPDATE provider_calls SET status=:st, error=:e, finished_at=:f WHERE id=:id"),
                {"st": status, "e": message[:500], "f": fin, "id": call_id},
            )
    return out
