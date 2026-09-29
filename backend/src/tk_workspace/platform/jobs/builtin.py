"""内置任务。system.noop 用来验证“创建 → 执行器领取 → 完成 → 重启恢复”的完整链路。"""

import time
from typing import Any

from tk_workspace.platform.jobs.registry import JobKind, JobRuntime, register


class Cancelled(Exception):
    pass


def noop_handler(rt: JobRuntime, params: dict[str, Any]) -> dict[str, Any]:
    sleep_ms = max(0, min(int(params.get("sleep_ms", 500)), 60_000))
    steps = 10
    for i in range(steps):
        time.sleep(sleep_ms / 1000 / steps)
        if not rt.report_progress({"percent": (i + 1) * 100 // steps, "message": "处理中"}):
            raise Cancelled("任务已取消或租约失效")
    if params.get("fail"):
        raise RuntimeError("noop job asked to fail")
    return {"echo": str(params.get("message", ""))[:200], "worker_id": rt.worker_id, "attempt": rt.attempt}


register(JobKind(name="system.noop", queue="default", handler=noop_handler, user_creatable=True))
