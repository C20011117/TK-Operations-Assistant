"""任务类型注册表。只有注册过的 kind 能被创建和执行。"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

QUEUE_KINDS = ("ingestion", "matching", "media_analysis", "external_actions", "maintenance", "default")


@dataclass
class JobRuntime:
    job_id: str
    worker_id: str
    attempt: int
    # 写入进度并续租；返回 False 表示租约已失效或用户请求了取消，handler 应尽快停止
    report_progress: Callable[[dict[str, Any]], bool]


Handler = Callable[[JobRuntime, dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class JobKind:
    name: str
    queue: str
    handler: Handler
    user_creatable: bool = False
    max_params_bytes: int = 4096
    # 应用在执行中被关闭后，重启时是否自动重新排队。对外动作（寄样登记等）必须为 False。
    retry_on_interrupt: bool = True
    max_attempts: int = 3


_REGISTRY: dict[str, JobKind] = {}


def register(kind: JobKind) -> None:
    assert kind.queue in QUEUE_KINDS, kind.queue
    _REGISTRY[kind.name] = kind


def get_kind(name: str) -> JobKind | None:
    _ensure_loaded()
    return _REGISTRY.get(name)


def _ensure_loaded() -> None:
    if not _REGISTRY:
        from tk_workspace.platform.jobs import builtin  # noqa: F401  注册内置任务
