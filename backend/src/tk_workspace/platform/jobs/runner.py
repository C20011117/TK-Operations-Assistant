"""进程内任务执行器：若干工作线程轮询 jobs 表领取任务。替代服务器版的 Celery + Redis + Outbox。"""

import logging
import os
import threading
import time
import uuid

from tk_workspace.config import get_settings
from tk_workspace.platform.jobs import executor
from tk_workspace.platform.jobs.service import job_enqueued

log = logging.getLogger(__name__)


class JobRunner:
    def __init__(self, workers: int | None = None, poll_seconds: float | None = None) -> None:
        s = get_settings()
        self.workers = workers or s.job_workers
        self.poll_seconds = poll_seconds or s.job_poll_seconds
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.instance_id = f"pid{os.getpid()}-{uuid.uuid4().hex[:6]}"

    def start(self) -> None:
        executor.recover_interrupted(on_startup=True)
        for i in range(self.workers):
            t = threading.Thread(
                target=self._loop, args=(f"{self.instance_id}:w{i}",), daemon=True, name=f"job-{i}"
            )
            t.start()
            self._threads.append(t)
        reaper = threading.Thread(target=self._reaper, daemon=True, name="job-reaper")
        reaper.start()
        self._threads.append(reaper)
        log.info("job runner started with %d worker(s)", self.workers)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        job_enqueued.set()
        for t in self._threads:
            t.join(timeout)

    def _loop(self, worker_id: str) -> None:
        while not self._stop.is_set():
            try:
                claim = executor.claim_next(worker_id)
            except Exception:  # noqa: BLE001  数据库暂时忙时稍后重试
                log.exception("claim failed")
                claim = None
            if claim is None:
                job_enqueued.wait(self.poll_seconds)
                job_enqueued.clear()
                continue
            executor.execute_claimed(claim, worker_id)

    def _reaper(self) -> None:
        """定期收回租约过期的任务（例如执行线程卡死）。"""
        while not self._stop.wait(30):
            try:
                executor.recover_interrupted(on_startup=False)
            except Exception:  # noqa: BLE001
                log.exception("reaper failed")


def wait_until(predicate, timeout: float = 10.0, interval: float = 0.05) -> bool:
    """测试辅助：等待条件成立。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False
