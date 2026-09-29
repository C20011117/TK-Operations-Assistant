"""Celery 入口。只在 Linux 容器中运行（Windows 原生 Celery 不作为支持环境，见 ADR-02）。"""

from celery import Celery

from tk_workspace.config import get_settings

celery_app = Celery("tk_workspace", broker=get_settings().redis_url)
celery_app.conf.update(
    task_default_queue="default",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    task_serializer="json",
    accept_content=["json"],
    timezone="UTC",
)
celery_app.autodiscover_tasks(["tk_workspace.workers"], related_name="tasks", force=True)
