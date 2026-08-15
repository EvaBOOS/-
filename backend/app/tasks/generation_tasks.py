"""Celery tasks wrapping async generation pipelines."""
from __future__ import annotations

import asyncio
import logging

from app.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Run async pipeline in a fresh event loop (Celery worker thread)."""
    return asyncio.run(coro)


async def _process_avatar(generation_id: int, client_id: int) -> None:
    from app.db.session import AsyncSessionLocal
    from app.services.pipeline import VideoGenerationPipeline

    async with AsyncSessionLocal() as db:
        await VideoGenerationPipeline(db).process(generation_id, client_id)


async def _process_viral(generation_id: int, client_id: int) -> None:
    from app.db.session import AsyncSessionLocal
    from app.services.viral_edit_pipeline import ViralEditPipeline

    async with AsyncSessionLocal() as db:
        await ViralEditPipeline(db).process(generation_id, client_id)


async def _process_clips(generation_id: int, client_id: int) -> None:
    from app.db.session import AsyncSessionLocal
    from app.services.clips_pipeline import AiClipsPipeline

    async with AsyncSessionLocal() as db:
        await AiClipsPipeline(db).process(generation_id, client_id)


@celery_app.task(name="videogen.process_avatar", bind=True, max_retries=1)
def process_avatar_task(self, generation_id: int, client_id: int):
    logger.info("Celery avatar job gen=%s client=%s", generation_id, client_id)
    try:
        _run_async(_process_avatar(generation_id, client_id))
    except Exception as exc:
        logger.exception("Avatar job failed gen=%s", generation_id)
        raise self.retry(exc=exc, countdown=30)


@celery_app.task(name="videogen.process_viral", bind=True, max_retries=1)
def process_viral_task(self, generation_id: int, client_id: int):
    logger.info("Celery viral job gen=%s client=%s", generation_id, client_id)
    try:
        _run_async(_process_viral(generation_id, client_id))
    except Exception as exc:
        logger.exception("Viral job failed gen=%s", generation_id)
        raise self.retry(exc=exc, countdown=30)


@celery_app.task(name="videogen.process_clips", bind=True, max_retries=1)
def process_clips_task(self, generation_id: int, client_id: int):
    logger.info("Celery clips job gen=%s client=%s", generation_id, client_id)
    try:
        _run_async(_process_clips(generation_id, client_id))
    except Exception as exc:
        logger.exception("Clips job failed gen=%s", generation_id)
        raise self.retry(exc=exc, countdown=30)


async def _process_trend_study(insight_id: int, client_id: int) -> None:
    from app.db.session import AsyncSessionLocal
    from app.services.trends.study_pipeline import TrendStudyPipeline

    async with AsyncSessionLocal() as db:
        await TrendStudyPipeline(db).process(insight_id, client_id)


@celery_app.task(name="videogen.process_trend_study", bind=True, max_retries=1)
def process_trend_study_task(self, insight_id: int, client_id: int):
    logger.info("Celery trend study insight=%s client=%s", insight_id, client_id)
    try:
        _run_async(_process_trend_study(insight_id, client_id))
    except Exception as exc:
        logger.exception("Trend study failed insight=%s", insight_id)
        raise self.retry(exc=exc, countdown=30)
