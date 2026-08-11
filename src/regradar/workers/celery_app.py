"""Celery application: connects to Redis as both broker and result backend.

Settings are resolved eagerly at import time (unlike core/db.py's lazy
singleton) — a worker process legitimately needs full config to do its
job, so failing fast here at startup is the correct behavior, not a
laziness regression. Nothing currently imports this module incidentally
without needing that config.
"""

from celery import Celery

from regradar.core.config import get_settings

_settings = get_settings()

celery_app = Celery(
    "regradar",
    broker=_settings.redis_url.get_secret_value(),
    backend=_settings.redis_url.get_secret_value(),
    include=["regradar.workers.pipeline_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)
