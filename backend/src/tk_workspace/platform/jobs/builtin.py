"""内置任务。M0 只有 system.noop，用来验证 API → outbox → 分发器 → Worker 的完整链路。"""

import time
from typing import Any

from tk_workspace.platform.jobs.registry import JobKind, JobRuntime, register


def noop_handler(rt: JobRuntime, params: dict[str, Any]) -> dict[str, Any]:
    sleep_ms = max(0, min(int(params.get("sleep_ms", 500)), 5000))
    time.sleep(sleep_ms / 2000)
    rt.report_progress({"percent": 50, "message": "处理中"})
    time.sleep(sleep_ms / 2000)
    if params.get("fail"):
        raise RuntimeError("noop job asked to fail")
    return {"echo": str(params.get("message", ""))[:200], "worker_id": rt.worker_id}


register(JobKind(name="system.noop", queue="default", handler=noop_handler, user_creatable=True))
