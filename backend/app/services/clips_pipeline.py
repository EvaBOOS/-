"""
AI clips pipeline: long video → Whisper → LLM highlights → cut Shorts (9:16).
"""
from __future__ import annotations

import os
import shutil
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models.client import Client
from app.models.generation import GenerationStatus, VideoGeneration
from app.services.ai.openai_service import OpenAIService
from app.services.ai.whisper_service import WhisperService
from app.services.assets.library_service import AssetLibraryService
from app.services.video.ffmpeg_service import FFmpegService
from app.services.source_resolve import ensure_local_source
from app.services.branding_watermark import resolve_export_watermark


class AiClipsPipeline:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.ffmpeg = FFmpegService()
        self.whisper = WhisperService()
        self.llm = OpenAIService()
        self.assets = AssetLibraryService()

    async def process(self, generation_id: int, client_id: int) -> Optional[str]:
        result = await self.db.execute(
            select(VideoGeneration).where(VideoGeneration.id == generation_id)
        )
        generation = result.scalar_one_or_none()
        if not generation:
            return None

        result = await self.db.execute(
            select(Client)
            .options(selectinload(Client.branding))
            .where(Client.id == client_id)
        )
        client = result.scalar_one_or_none()
        if not client:
            await self._fail(generation, "Client not found")
            return None

        source = await ensure_local_source(
            self.db,
            generation,
            upload_subdir="clips",
            max_bytes=settings.MAX_CLIPS_UPLOAD_SIZE,
            label_prefix="[ai_clips]",
        )
        if not source:
            return None

        generation.started_at = datetime.utcnow()
        generation.api_responses = generation.api_responses or {}
        max_clips = int(
            generation.api_responses.get("max_clips") or settings.AI_CLIPS_MAX_COUNT
        )
        await self.db.commit()

        work_dir = os.path.join(
            settings.GENERATED_DIR, "clips", str(client_id), str(generation_id)
        )
        os.makedirs(work_dir, exist_ok=True)

        try:
            await self._update(generation, GenerationStatus.TRANSCRIPTION, progress=10)
            audio_path = os.path.join(work_dir, "speech.mp3")
            self.ffmpeg.extract_audio_track(source, audio_path)
            generation.audio_path = audio_path

            transcript = await self.whisper.transcribe_long(
                audio_path, language=generation.target_language or "ru"
            )
            text = transcript.get("text") or ""
            segments = transcript.get("segments") or []
            words = transcript.get("words") or []
            duration = float(
                transcript.get("duration") or self.ffmpeg.get_video_duration(source)
            )

            if text:
                generation.original_text = text[:4000]
                generation.generated_script = text[:8000]
            generation.api_responses["transcription"] = {
                "provider": transcript.get("provider"),
                "words": len(words),
                "segments": len(segments),
                "duration": duration,
                "chunks": transcript.get("chunks"),
            }
            generation.progress_percent = 40
            await self.db.commit()

            if not text:
                await self._fail(generation, "Не удалось распознать речь в длинном видео")
                return None

            await self._update(generation, GenerationStatus.CLIPPING, progress=50)
            try:
                plan = await self.llm.plan_ai_clips(
                    transcript=text,
                    segments=segments,
                    duration=duration,
                    language=generation.target_language or "ru",
                    max_clips=max_clips,
                )
            except Exception as e:
                plan = []
                generation.api_responses["clip_plan_error"] = str(e)[:300]

            if not plan:
                await self._fail(generation, "Не удалось выбрать клипы из видео")
                return None

            generation.api_responses["clip_plan"] = plan
            await self.db.commit()

            await self._update(generation, GenerationStatus.VIDEO_PROCESSING, progress=60)
            font_family = "Arial"
            fonts_dir = None
            chosen_font_id = str((generation.api_responses or {}).get("font_id") or "").strip()
            branding = client.branding

            if chosen_font_id:
                try:
                    font_family, fonts_dir, font_file = await self.assets.ensure_font_file(
                        chosen_font_id
                    )
                    generation.api_responses["font_id"] = chosen_font_id
                    generation.api_responses["font_path"] = font_file
                except Exception:
                    chosen_font_id = ""

            if not chosen_font_id and branding and branding.subtitle_font_path and os.path.isfile(
                branding.subtitle_font_path
            ):
                font_family = branding.subtitle_font_name or "Custom"
                fonts_dir = os.path.dirname(branding.subtitle_font_path)
                generation.api_responses["font_id"] = "branding"
                generation.api_responses["font_path"] = branding.subtitle_font_path
            elif not chosen_font_id and settings.AUTO_FONT_ENABLED:
                try:
                    font_family, fonts_dir, font_id = await self.assets.pick_font_for_text(text)
                    generation.api_responses["font_id"] = font_id
                except Exception:
                    pass

            clips_out = []
            for i, clip in enumerate(plan):
                raw = os.path.join(work_dir, f"raw_{i+1}.mp4")
                framed = os.path.join(work_dir, f"clip_{i+1}.mp4")
                self.ffmpeg.cut_clip(source, raw, clip["start"], clip["end"])
                self.ffmpeg.scale_to_format(
                    raw,
                    framed,
                    width=settings.VIDEO_WIDTH,
                    height=settings.VIDEO_HEIGHT,
                )

                # Optional karaoke for this window
                local_words = [
                    {
                        "word": w["word"],
                        "start": max(0.0, float(w["start"]) - float(clip["start"])),
                        "end": max(0.05, float(w["end"]) - float(clip["start"])),
                    }
                    for w in words
                    if float(w.get("end") or 0) >= float(clip["start"])
                    and float(w.get("start") or 0) <= float(clip["end"])
                ]
                final_clip = framed
                if local_words:
                    ass = os.path.join(work_dir, f"clip_{i+1}.ass")
                    self.ffmpeg.write_karaoke_ass(
                        words=local_words,
                        output_path=ass,
                        highlight_words=[],
                        font_name=font_family,
                        video_width=settings.VIDEO_WIDTH,
                        video_height=settings.VIDEO_HEIGHT,
                        hook_text=clip.get("title"),
                        hook_duration=2.2,
                    )
                    subtitled = os.path.join(work_dir, f"clip_{i+1}_sub.mp4")
                    try:
                        self.ffmpeg.burn_ass_subtitles(
                            framed, ass, subtitled, fonts_dir=fonts_dir
                        )
                        final_clip = subtitled
                    except Exception:
                        final_clip = framed

                out_name = f"final_clip_{i+1}.mp4"
                out_path = os.path.join(work_dir, out_name)
                wm = resolve_export_watermark(client)
                if wm and wm.get("path"):
                    marked = os.path.join(work_dir, f"clip_{i+1}_wm.mp4")
                    self.ffmpeg.apply_watermark(
                        final_clip,
                        marked,
                        watermark_path=wm["path"],
                        watermark_position=wm.get("position") or "bottom_right",
                        watermark_opacity=int(wm.get("opacity") or 70),
                        watermark_scale=int(wm.get("scale") or 13),
                    )
                    shutil.copy2(marked, out_path)
                    if i == 0:
                        generation.api_responses["watermark"] = {
                            "kind": wm.get("kind"),
                            "applied": True,
                        }
                else:
                    shutil.copy2(final_clip, out_path)
                    if i == 0:
                        generation.api_responses["watermark"] = {
                            "kind": None,
                            "applied": False,
                            "reason": "premium_clean",
                        }
                clips_out.append({
                    "index": i + 1,
                    "title": clip.get("title"),
                    "reason": clip.get("reason"),
                    "score": clip.get("score"),
                    "start": clip.get("start"),
                    "end": clip.get("end"),
                    "path": out_path,
                    "duration": round(float(clip["end"]) - float(clip["start"]), 1),
                    "file_size_bytes": self.ffmpeg.get_file_size(out_path),
                })
                generation.progress_percent = 60 + int(35 * (i + 1) / max(len(plan), 1))
                await self.db.commit()

            generation.api_responses["clips"] = [
                {**c, "path": os.path.basename(c["path"])} for c in clips_out
            ]
            # Keep absolute paths internally for download helper
            generation.api_responses["clips_abs"] = [c["path"] for c in clips_out]

            best = clips_out[0]["path"]
            generation.final_video_path = best
            generation.duration_seconds = int(self.ffmpeg.get_video_duration(best))
            generation.file_size_bytes = self.ffmpeg.get_file_size(best)
            generation.progress_percent = 100
            generation.status = GenerationStatus.COMPLETED
            generation.completed_at = datetime.utcnow()

            if not generation.credit_deducted:
                client.credits_remaining -= 1
                client.credits_used_this_month += 1
                generation.credit_deducted = True

            await self.db.commit()
            return best

        except Exception as e:
            await self._fail(generation, str(e)[:500])
            return None

    async def _update(
        self,
        generation: VideoGeneration,
        status: GenerationStatus,
        progress: int = None,
        error: str = None,
    ):
        generation.status = status
        if progress is not None:
            generation.progress_percent = progress
        if error:
            generation.error_message = error
        generation.api_responses = generation.api_responses or {}
        generation.api_responses[status.value] = {
            "timestamp": datetime.utcnow().isoformat(),
            "error": error,
        }
        await self.db.commit()

    async def _fail(self, generation: VideoGeneration, message: str):
        await self._update(
            generation,
            GenerationStatus.FAILED,
            error=message,
            progress=generation.progress_percent or 0,
        )
