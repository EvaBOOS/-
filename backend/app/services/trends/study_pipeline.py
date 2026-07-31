"""Analyze a trend URL: download → Whisper → LLM insight card."""
from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models.client import Client
from app.models.trend import TrendInsight, TrendItem
from app.services.ai.openai_service import OpenAIService
from app.services.ai.whisper_service import WhisperService
from app.services.media_ingest import MediaIngestError, download_video_from_url
from app.services.video.ffmpeg_service import FFmpegService


class TrendStudyPipeline:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.ffmpeg = FFmpegService()
        self.whisper = WhisperService()
        self.llm = OpenAIService()

    async def process(self, insight_id: int, client_id: int) -> Optional[int]:
        result = await self.db.execute(
            select(TrendInsight).where(TrendInsight.id == insight_id)
        )
        insight = result.scalar_one_or_none()
        if not insight:
            return None

        result = await self.db.execute(
            select(Client)
            .options(selectinload(Client.branding))
            .where(Client.id == client_id)
        )
        client = result.scalar_one_or_none()
        if not client:
            await self._fail(insight, "Client not found")
            return None

        insight.status = "processing"
        insight.started_at = datetime.utcnow()
        insight.progress_percent = 5
        insight.error_message = None
        await self.db.commit()

        work_dir = os.path.join(
            settings.GENERATED_DIR, "trends", str(client_id), str(insight_id)
        )
        os.makedirs(work_dir, exist_ok=True)

        try:
            # 1) Download
            insight.progress_percent = 15
            await self.db.commit()
            try:
                path, display = await download_video_from_url(
                    insight.source_url,
                    work_dir,
                    max_bytes=settings.MAX_VIRAL_UPLOAD_SIZE,
                )
            except MediaIngestError as exc:
                # TikTok hashtag pages often aren't direct videos
                await self._fail(
                    insight,
                    f"Не удалось скачать видео. Нужна прямая ссылка на ролик. ({exc})",
                )
                return None

            insight.source_video_path = path
            insight.progress_percent = 35
            await self.db.commit()

            # 2) Whisper
            audio = os.path.join(work_dir, "speech.mp3")
            self.ffmpeg.extract_audio_track(path, audio)
            duration = float(self.ffmpeg.get_video_duration(path) or 0)
            transcript = await self.whisper.transcribe(audio, language="ru")
            text = (transcript.get("text") or "").strip()
            if not text:
                transcript = await self.whisper.transcribe(audio, language="en")
                text = (transcript.get("text") or "").strip()
            if not text:
                await self._fail(insight, "Не удалось распознать речь в ролике")
                return None

            words = transcript.get("words") or []
            if duration <= 0:
                duration = float(transcript.get("duration") or 0)
            insight.duration_sec = duration
            insight.progress_percent = 65
            await self.db.commit()

            # Approximate WPM
            word_count = len(words) if words else max(1, len(text.split()))
            pace = (word_count / max(duration, 1.0)) * 60.0

            title = ""
            if insight.trend_item_id:
                tr = await self.db.execute(
                    select(TrendItem).where(TrendItem.id == insight.trend_item_id)
                )
                item = tr.scalar_one_or_none()
                if item:
                    title = item.title or ""

            # 3) LLM
            insight.progress_percent = 80
            await self.db.commit()
            analysis = await self.llm.analyze_trend_video(
                transcript=text,
                duration=duration,
                language=(transcript.get("language") or "ru")[:10],
                title=title or display,
            )

            insight.transcript_summary = analysis.get("summary")
            insight.hook_text = analysis.get("hook_text")
            insight.style_guess = analysis.get("style_guess")
            insight.pace_wpm = float(analysis.get("pace_wpm") or pace)
            insight.tips = analysis.get("tips") or []
            insight.raw_llm = {
                **analysis,
                "word_count": word_count,
                "display_name": display,
            }
            insight.status = "completed"
            insight.progress_percent = 100
            insight.completed_at = datetime.utcnow()

            cost = max(0, int(settings.TRENDS_ANALYZE_COST_CREDITS or 0))
            if cost and not insight.credit_deducted and client.credits_remaining >= cost:
                client.credits_remaining -= cost
                client.credits_used_this_month += cost
                insight.credit_deducted = True

            await self.db.commit()
            return insight.id

        except Exception as exc:
            await self._fail(insight, str(exc)[:500])
            return None

    async def _fail(self, insight: TrendInsight, message: str) -> None:
        insight.status = "failed"
        insight.error_message = message
        insight.progress_percent = insight.progress_percent or 0
        await self.db.commit()
