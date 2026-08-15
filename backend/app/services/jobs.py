"""Dispatch generation jobs to Celery (prod) or FastAPI BackgroundTasks (local)."""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from fastapi import BackgroundTasks

from app.core.config import settings

logger = logging.getLogger(__name__)


def celery_enabled() -> bool:
    """Use Celery when explicitly enabled or when not on SQLite local mode."""
    if settings.USE_CELERY is True:
        return True
    if settings.USE_CELERY is False:
        return False
    # Auto: Celery for non-sqlite (typical docker/prod)
    return not settings.DATABASE_URL.startswith("sqlite")


def _ping_redis() -> bool:
    try:
        import redis

        client = redis.from_url(settings.REDIS_URL, socket_connect_timeout=1.5)
        return bool(client.ping())
    except Exception:
        return False


def enqueue_job(
    background_tasks: BackgroundTasks,
    kind: str,
    generation_id: int,
    client_id: int,
    local_coro_runner: Callable[..., Any],
) -> str:
    """
    Enqueue avatar | viral | clips | trend_study job.
    Returns backend used: "celery" | "background".
    """
    use_celery = celery_enabled() and _ping_redis()
    if use_celery:
        from app.tasks.generation_tasks import (
            process_avatar_task,
            process_clips_task,
            process_trend_study_task,
            process_viral_task,
        )

        mapping = {
            "avatar": process_avatar_task,
            "viral": process_viral_task,
            "clips": process_clips_task,
            "trend_study": process_trend_study_task,
        }
        task = mapping.get(kind)
        if not task:
            raise ValueError(f"Unknown job kind: {kind}")
        async_result = task.delay(generation_id, client_id)
        logger.info(
            "Enqueued %s via Celery id=%s entity=%s",
            kind,
            async_result.id,
            generation_id,
        )
        return "celery"

    background_tasks.add_task(local_coro_runner, generation_id, client_id)
    logger.info("Enqueued %s via BackgroundTasks entity=%s", kind, generation_id)
    return "background"


def requeue_generation(
    kind: str,
    generation_id: int,
    client_id: int,
    background_tasks: BackgroundTasks,
) -> str:
    """Re-enqueue after a moderator approves a hold. Lazy-imports runners to avoid cycles."""
    from app.api.v1.endpoints.client import (
        process_ai_clips,
        process_video_generation,
        process_viral_edit,
    )

    runners = {
        "avatar": process_video_generation,
        "viral": process_viral_edit,
        "clips": process_ai_clips,
    }
    runner = runners.get(kind)
    if not runner:
        raise ValueError(f"Unknown job kind: {kind}")
    return enqueue_job(background_tasks, kind, generation_id, client_id, runner)

