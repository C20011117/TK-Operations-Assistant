from tk_workspace.platform.jobs.executor import execute_job
from tk_workspace.workers.celery_app import celery_app


@celery_app.task(name="tk.run_job")
def run_job(job_id: str) -> str:
    return execute_job(job_id)
