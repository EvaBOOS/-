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
from app.services.video.content_presets import normalize_platform


def _snap_to_word_boundary(t: float, words: list, edge: str, max_shift: float = 1.5) -> float:
    """
    Snap an LLM-picked cut point to the nearest actual word start/end so cuts
    don't land mid-word. The LLM only ever saw sentence-level segments (or a
    truncated transcript), so its numeric start/end guesses are frequently
    off by a fraction of a second — enough to slice through a word.
    """
    if not words:
        return t
    if edge == "start":
        candidates = [
            float(w["start"]) for w in words
            if float(w.get("start", 0)) <= t + 0.05
        ]
        if not candidates:
            return t
        best = max(candidates)
        return best if t - best <= max_shift else t
    candidates = [
        float(w["end"]) for w in words
        if float(w.get("end", 0)) >= t - 0.05
    ]
    if not candidates:
        return t
    best = min(candidates)
    return best if best - t <= max_shift else t


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
        platform = normalize_platform(generation.api_responses.get("platform"))
        generation.api_responses["platform"] = platform
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
                    platform=platform,
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
            emphasis_style = (branding.subtitle_emphasis_style if branding else None) or "color"
            accent_color = branding.subtitle_accent_color if branding else None

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
                snapped_start = _snap_to_word_boundary(float(clip["start"]), words, "start")
                snapped_end = _snap_to_word_boundary(float(clip["end"]), words, "end")
                if snapped_end - snapped_start >= 2.0:
                    clip["start"], clip["end"] = snapped_start, snapped_end
                self.ffmpeg.cut_clip(source, raw, clip["start"], clip["end"])
                self.ffmpeg.scale_to_format(
                    raw,
                    framed,
                    width=settings.VIDEO_WIDTH,
                    height=settings.VIDEO_HEIGHT,
                    face_aware=True,
                )
                polished = os.path.join(work_dir, f"clip_{i+1}_audio.mp4")
                self.ffmpeg.prepare_program_audio(framed, polished)
                framed = polished

                track_points = None
                if generation.api_responses.get("kinetic_subtitles"):
                    try:
                        from app.services.video.face_tracking import FaceTrackingService
                        track_points = FaceTrackingService().track_face_centers(framed)
                    except Exception as track_err:
                        track_points = None
                        generation.api_responses["kinetic_subtitles_error"] = str(track_err)[:300]

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
                clip_hook_text = clip.get("title")
                hook_3d_path = None
                if generation.api_responses.get("volumetric_hook") and clip_hook_text:
                    try:
                        from app.services.video.text3d_service import render_3d_text_image
                        png_bytes = await render_3d_text_image(
                            clip_hook_text, font_path=generation.api_responses.get("font_path"),
                            max_width=int(settings.VIDEO_WIDTH * 0.9),
                        )
                        hook_3d_path = os.path.join(work_dir, f"clip_{i+1}_hook3d.png")
                        with open(hook_3d_path, "wb") as f:
                            f.write(png_bytes)
                    except Exception as hook3d_err:
                        hook_3d_path = None
                        generation.api_responses["volumetric_hook_error"] = str(hook3d_err)[:300]

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
                        hook_text=(None if hook_3d_path else clip_hook_text),
                        hook_duration=2.2,
                        emphasis_style=emphasis_style,
                        track_points=track_points,
                        accent_color=accent_color,
                    )
                    subtitled = os.path.join(work_dir, f"clip_{i+1}_sub.mp4")
                    try:
                        self.ffmpeg.burn_ass_subtitles(
                            framed, ass, subtitled, fonts_dir=fonts_dir
                        )
                        final_clip = subtitled
                    except Exception:
                        final_clip = framed

                    if hook_3d_path:
                        hooked = os.path.join(work_dir, f"clip_{i+1}_hooked.mp4")
                        try:
                            self.ffmpeg.overlay_timed_image(
                                final_clip, hook_3d_path, hooked, start=0, end=2.2
                            )
                            final_clip = hooked
                        except Exception as overlay_err:
                            generation.api_responses["volumetric_hook_error"] = str(overlay_err)[:300]

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
                        on_fallback=lambda m, idx=i: generation.api_responses.setdefault(
                            "edit_warnings", []
                        ).append(f"clip {idx + 1}: {m}"),
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
                    "moment": clip.get("moment") or "other",
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
            try:
                generation.api_responses["qc"] = self.ffmpeg.qc_export(best)
            except Exception:
                pass
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
