"""
Celery application for VideoGen background jobs.

Broker/backend: Redis (REDIS_URL).
Workers: `celery -A app.celery_app worker --loglevel=info`
"""
from __future__ import annotations

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "videogen",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks.generation_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    # Long video jobs (Whisper / HeyGen / FFmpeg)
    task_soft_time_limit=60 * 45,
    task_time_limit=60 * 60,
)
