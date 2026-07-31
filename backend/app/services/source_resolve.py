"""Shared helper: ensure generation has a local source video (upload or link ingest)."""
from __future__ import annotations

import os
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.generation import GenerationStatus, VideoGeneration
from app.services.media_ingest import MediaIngestError, download_video_from_url


async def ensure_local_source(
    db: AsyncSession,
    generation: VideoGeneration,
    *,
    upload_subdir: str,
    max_bytes: int,
    label_prefix: str,
) -> Optional[str]:
    """
    Return path to local source file.
    If only source_url is set, download in the worker and persist path.
    On failure marks generation FAILED and returns None.
    """
    path = generation.source_video_path
    if path and os.path.isfile(path):
        return path

    url = str((generation.api_responses or {}).get("source_url") or "").strip()
    if not url:
        generation.status = GenerationStatus.FAILED
        generation.error_message = "Исходное видео не найдено"
        await db.commit()
        return None

    generation.status = GenerationStatus.PENDING
    generation.progress_percent = 5
    generation.api_responses = generation.api_responses or {}
    generation.api_responses["ingest"] = {"status": "downloading", "url": url}
    await db.commit()

    dest_dir = os.path.join(settings.UPLOAD_DIR, upload_subdir, str(generation.client_id))
    try:
        path, display = await download_video_from_url(url, dest_dir, max_bytes)
    except MediaIngestError as exc:
        generation.status = GenerationStatus.FAILED
        generation.error_message = str(exc)[:500]
        generation.api_responses["ingest"] = {"status": "failed", "error": str(exc)[:300]}
        await db.commit()
        return None
    except Exception as exc:
        generation.status = GenerationStatus.FAILED
        generation.error_message = f"Ошибка скачивания: {exc}"[:500]
        generation.api_responses["ingest"] = {"status": "failed", "error": str(exc)[:300]}
        await db.commit()
        return None

    generation.source_video_path = path
    generation.original_text = f"{label_prefix} {display}"[:500]
    generation.progress_percent = 12
    generation.api_responses["ingest"] = {
        "status": "ok",
        "path": os.path.basename(path),
        "title": display,
    }
    await db.commit()
    return path
