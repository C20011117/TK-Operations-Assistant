"""Outbox 分发器：把已提交的 job.enqueued 事件投递到 Celery。

使用只能读写 outbox_events 的 app_dispatcher 角色，读不到企业业务正文。
至少一次投递；投递失败保留未发布状态等待下次重试。Redis 丢失时可以从数据库重新投递。
"""

import logging
import signal
import time
from collections.abc import Callable

from sqlalchemy import text

from tk_workspace.platform.db.session import dispatcher_tx

log = logging.getLogger(__name__)

Publisher = Callable[[str, str], None]  # (job_id, queue)


def celery_publisher(job_id: str, queue: str) -> None:
    from tk_workspace.workers.celery_app import celery_app

    celery_app.send_task("tk.run_job", args=[job_id], queue=queue or "default")


def run_once(publish: Publisher = celery_publisher, limit: int = 50) -> int:
    published = 0
    with dispatcher_tx() as s:
        rows = s.execute(
            text(
                """SELECT id, payload FROM outbox_events
                   WHERE published_at IS NULL AND event_type = 'job.enqueued' AND attempts < 20
                   ORDER BY occurred_at
                   LIMIT :n FOR UPDATE SKIP LOCKED"""
            ),
            {"n": limit},
        ).all()
        for r in rows:
            try:
                publish(str(r.payload["job_id"]), str(r.payload.get("queue", "default")))
            except Exception as exc:  # noqa: BLE001
                log.warning("publish failed for outbox %s: %s", r.id, exc)
                s.execute(
                    text("UPDATE outbox_events SET attempts = attempts + 1, last_error = :e WHERE id = :id"),
                    {"e": str(exc)[:300], "id": r.id},
                )
                continue
            s.execute(
                text("UPDATE outbox_events SET published_at = now(), attempts = attempts + 1 WHERE id = :id"),
                {"id": r.id},
            )
            published += 1
    return published


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s dispatcher %(message)s")
    stop = False

    def _stop(*_: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info("outbox dispatcher started")
    while not stop:
        try:
            n = run_once()
            if n:
                log.info("published %d event(s)", n)
        except Exception:  # noqa: BLE001  数据库暂时不可用时稍后重试
            log.exception("dispatch round failed")
            time.sleep(3)
        time.sleep(0.5 if not stop else 0)
    log.info("outbox dispatcher stopped")


if __name__ == "__main__":
    main()
