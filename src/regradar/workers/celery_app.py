"""Celery application instance.

Minimal placeholder — task definitions and full broker/backend configuration
are implemented in ING-06. This stub exists only so the `worker` service in
Docker Compose (FOUND-03) has something real to run.
"""

import os

from celery import Celery

celery_app = Celery(
    "regradar",
    broker=os.environ["REDIS_URL"],
    backend=os.environ["REDIS_URL"],
)
