"""
Viral edit pipeline Stage 2:
upload → cut silences → 9:16 → Whisper → LLM plan
→ zooms → B-roll → karaoke+hook → stickers → music ducking
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
from app.services.ai.edge_tts_service import EdgeTTSService
from app.services.assets.library_service import AssetLibraryService
from app.services.assets.music_service import MusicLibraryService
from app.services.video.ffmpeg_service import FFmpegService
from app.services.video.edit_styles import ensure_min_zooms, get_style
from app.services.source_resolve import ensure_local_source
from app.services.branding_watermark import resolve_export_watermark


class ViralEditPipeline:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.ffmpeg = FFmpegService()
        self.whisper = WhisperService()
        self.llm = OpenAIService()
        self.assets = AssetLibraryService()
        self.music = MusicLibraryService()
        self.tts = EdgeTTSService()

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
            upload_subdir="viral",
            max_bytes=settings.MAX_VIRAL_UPLOAD_SIZE,
            label_prefix="[viral_edit]",
        )
        if not source:
            return None

        generation.started_at = datetime.utcnow()
        generation.api_responses = generation.api_responses or {}
        edit_style = str(generation.api_responses.get("edit_style") or "dynamic")
        edit_format = str(generation.api_responses.get("edit_format") or "9:16")
        dub_language = str(generation.api_responses.get("dub_language") or "").strip().lower()
        speech_language = (generation.target_language or "ru").strip().lower()
        style_cfg = get_style(edit_style)
        width, height = self._format_size(edit_format)
        await self.db.commit()

        work_dir = os.path.join(
            settings.GENERATED_DIR, "viral", str(client_id), str(generation_id)
        )
        os.makedirs(work_dir, exist_ok=True)

        try:
            # 1) Soft silence cut → target format
            await self._update(generation, GenerationStatus.VIRAL_EDIT, progress=8)
            silences = self.ffmpeg.detect_silence_intervals(
                source, noise_db=-30.0, min_silence=0.7
            )
            cut_path = os.path.join(work_dir, "cut.mp4")
            self.ffmpeg.keep_speech_segments(
                source,
                cut_path,
                silences,
                min_keep=0.35,
                pad=0.2,
                max_cut_ratio=0.35,
                min_silence_len=0.55,
            )
            generation.api_responses["silence_cuts"] = len(silences)
            generation.api_responses["edit_format"] = edit_format

            framed_path = os.path.join(work_dir, "framed.mp4")
            self.ffmpeg.scale_to_format(
                cut_path,
                framed_path,
                width=width,
                height=height,
            )
            source_dark = self.ffmpeg.is_mostly_dark(framed_path)
            generation.api_responses["source_dark"] = source_dark
            await self.db.commit()

            # 2) Whisper (optional — silent clips are valid)
            await self._update(generation, GenerationStatus.TRANSCRIPTION, progress=28)
            audio_path = os.path.join(work_dir, "speech.mp3")
            self.ffmpeg.extract_audio_track(framed_path, audio_path)
            generation.audio_path = audio_path

            voiceover_text = str(
                generation.api_responses.get("voiceover_text") or ""
            ).strip()

            text = ""
            words: list = []
            try:
                transcript = await self.whisper.transcribe(
                    audio_path, language=speech_language
                )
                text = (transcript.get("text") or "").strip()
                words = transcript.get("words") or []
                generation.api_responses["transcription"] = {
                    "provider": transcript.get("provider"),
                    "model": transcript.get("model"),
                    "words": len(words),
                    "duration": transcript.get("duration"),
                    "mode": "speech" if text else "silent",
                }
                duration = float(
                    transcript.get("duration")
                    or self.ffmpeg.get_video_duration(framed_path)
                )
            except Exception as tr_err:
                duration = float(self.ffmpeg.get_video_duration(framed_path) or 0)
                generation.api_responses["transcription"] = {
                    "mode": "failed",
                    "error": str(tr_err)[:240],
                }

            # Optional: user-supplied voiceover when source has no speech
            if not text and voiceover_text and len(voiceover_text) > 2:
                await self._update(generation, GenerationStatus.VOICE_SYNTHESIS, progress=42)
                try:
                    vo_audio = await self.tts.synthesize_speech(
                        text=voiceover_text[:4000],
                        voice_id="",
                        output_dir=work_dir,
                        language=speech_language,
                    )
                    voiced = os.path.join(work_dir, "voiced.mp4")
                    self.ffmpeg.combine_audio_video(
                        framed_path, vo_audio, voiced, fit_to_audio=True
                    )
                    framed_path = voiced
                    text = voiceover_text[:4000]
                    generation.generated_script = text
                    generation.original_text = text
                    duration = float(self.ffmpeg.get_video_duration(framed_path))
                    try:
                        vo_speech = os.path.join(work_dir, "vo_speech.mp3")
                        self.ffmpeg.extract_audio_track(framed_path, vo_speech)
                        generation.audio_path = vo_speech
                        vo_tr = await self.whisper.transcribe(
                            vo_speech, language=speech_language
                        )
                        words = vo_tr.get("words") or []
                        if not words:
                            words = WhisperService._approximate_words(text, duration)
                    except Exception:
                        words = WhisperService._approximate_words(text, duration)
                    generation.api_responses["transcription"] = {
                        "mode": "voiceover_overlay",
                        "words": len(words),
                        "duration": duration,
                    }
                    generation.api_responses["voiceover"] = {"ok": True, "chars": len(text)}
                except Exception as vo_err:
                    generation.api_responses["voiceover"] = {
                        "ok": False,
                        "error": str(vo_err)[:300],
                    }

            no_speech = not bool(text and text.strip())
            if text:
                generation.original_text = text
                generation.generated_script = text
            elif no_speech:
                generation.api_responses["transcription"] = {
                    **(generation.api_responses.get("transcription") or {}),
                    "mode": "silent",
                    "note": "В видео нет речи — визуальный монтаж без караоке-субтитров",
                }
                # Louder bed when there is nothing to duck under
                style_cfg = {
                    **style_cfg,
                    "music_vol": min(0.35, float(style_cfg.get("music_vol") or 0.14) * 1.6),
                }

            generation.progress_percent = 40
            await self.db.commit()

            plan_language = speech_language

            # 2b) Optional dubbing to another language (needs source speech/text)
            if (
                not no_speech
                and dub_language
                and dub_language != speech_language[:2]
                and dub_language != speech_language
            ):
                await self._update(generation, GenerationStatus.VOICE_SYNTHESIS, progress=48)
                try:
                    translated = await self.llm.translate_script(
                        text=text,
                        target_language=dub_language,
                        source_language=speech_language,
                    )
                    if translated and len(translated.strip()) > 5:
                        dub_audio = await self.tts.synthesize_speech(
                            text=translated.strip(),
                            voice_id="",
                            output_dir=work_dir,
                            language=dub_language,
                        )
                        dubbed_video = os.path.join(work_dir, "dubbed.mp4")
                        # Fit picture to dub length (freeze-frame if TTS longer)
                        self.ffmpeg.combine_audio_video(
                            framed_path, dub_audio, dubbed_video, fit_to_audio=True
                        )
                        framed_path = dubbed_video
                        text = translated.strip()
                        generation.generated_script = text
                        duration = float(self.ffmpeg.get_video_duration(framed_path))
                        plan_language = dub_language

                        # Re-time captions to the new dubbed audio (Whisper → fallback approx)
                        try:
                            dub_speech = os.path.join(work_dir, "dub_speech.mp3")
                            self.ffmpeg.extract_audio_track(framed_path, dub_speech)
                            dub_tr = await self.whisper.transcribe(
                                dub_speech, language=dub_language
                            )
                            words = dub_tr.get("words") or []
                            if not words:
                                words = WhisperService._approximate_words(text, duration)
                            duration = float(
                                dub_tr.get("duration")
                                or duration
                                or self.ffmpeg.get_video_duration(framed_path)
                            )
                            generation.api_responses["dub_timing"] = {
                                "provider": dub_tr.get("provider"),
                                "words": len(words),
                                "source": "whisper" if dub_tr.get("words") else "approx",
                            }
                        except Exception:
                            words = WhisperService._approximate_words(text, duration)
                            generation.api_responses["dub_timing"] = {
                                "words": len(words),
                                "source": "approx",
                            }

                        generation.api_responses["dubbing"] = {
                            "from": speech_language,
                            "to": dub_language,
                            "ok": True,
                        }
                    else:
                        generation.api_responses["dubbing"] = {
                            "from": speech_language,
                            "to": dub_language,
                            "ok": False,
                            "error": "empty translation",
                        }
                except Exception as dub_err:
                    generation.api_responses["dubbing"] = {
                        "from": speech_language,
                        "to": dub_language,
                        "ok": False,
                        "error": str(dub_err)[:300],
                    }
                await self.db.commit()
            elif no_speech and dub_language:
                generation.api_responses["dubbing"] = {
                    "skipped": True,
                    "reason": "no_speech",
                }

            # 3) Rich edit plan
            await self._update(generation, GenerationStatus.VIRAL_EDIT, progress=55)
            plan_transcript = text
            if no_speech:
                plan_transcript = (
                    f"[SILENT visual clip, duration={duration:.1f}s, has_picture=yes]. "
                    "There is NO speech and NO need for on-screen clickbait text. "
                    "Return hook.use=false and text=\"\". "
                    "Do NOT invent thematic stock B-roll (no concerts/DJ/crowds unless transcript mentions them). "
                    "broll must be []. Only timed zoom effects across the duration."
                )
            try:
                plan = await self.llm.plan_viral_edit(
                    transcript=plan_transcript,
                    words=words,
                    duration=duration,
                    language=plan_language,
                    style=edit_style,
                )
            except Exception as plan_err:
                plan = {
                    "effects": [],
                    "broll": [],
                    "hook": {"use": False, "text": ""},
                    "mood": style_cfg.get("mood_default", "energetic"),
                    "style": edit_style,
                }
                generation.api_responses["edit_plan_error"] = str(plan_err)[:300]

            effects = plan.get("effects") or []
            if no_speech:
                # No captions → no highlight words; no random thematic inserts
                effects = [
                    e for e in effects
                    if not (
                        isinstance(e, dict) and e.get("effect") == "highlight_word"
                    )
                ]
                plan["broll"] = []
            zooms = [
                e for e in effects
                if isinstance(e, dict) and e.get("effect") == "zoom"
            ][: int(style_cfg.get("zoom_cap") or 5)]
            zooms = ensure_min_zooms(
                zooms,
                words=words,
                duration=float(duration or 0),
                style_cfg=style_cfg,
            )
            highlights = [
                str(e.get("word"))
                for e in effects
                if isinstance(e, dict) and e.get("effect") == "highlight_word" and e.get("word")
            ]

            hook = plan.get("hook") if isinstance(plan.get("hook"), dict) else None
            if no_speech:
                # Silent real footage: never burn silly AI slogans ("Музыкальный взрыв!")
                hook = {"use": False, "text": ""}
            elif style_cfg.get("force_hook") and (not hook or not hook.get("use") or not hook.get("text")):
                if plan_language.startswith("ru"):
                    hook = {"use": True, "text": "Смотри до конца — это важно"}
                else:
                    hook = {"use": True, "text": "Watch this before you scroll"}
            mood = plan.get("mood") or style_cfg.get("mood_default") or "energetic"
            generation.api_responses["edit_plan"] = {
                "style": edit_style,
                "mood": mood,
                "format": edit_format,
                "dub_language": dub_language or None,
                "no_speech": no_speech,
                "effects": effects[:20],
                "broll": plan.get("broll") or [],
                "hook": hook,
            }
            await self.db.commit()

            # 4) Visual base: if source is black/placeholder, build photo slideshow first
            await self._update(generation, GenerationStatus.VIDEO_PROCESSING, progress=62)
            photo_paths: list = []
            inserts = []
            broll_max = int(style_cfg.get("broll_max") or 3)
            cues = [c for c in (plan.get("broll") or []) if isinstance(c, dict)]
            # Silent clips: never invent stock overlays / slideshow — keep the user's frame
            # even if is_mostly_dark (evening party lights often trip that heuristic).
            if no_speech:
                cues = []
            elif source_dark or not cues:
                # Extra theme queries when canvas is empty — need full coverage
                theme_q = (text or "lifestyle")[:80]
                cues = (cues + [
                    {"query": theme_q, "time": 0, "duration": 2},
                    {"query": "cinematic lifestyle photo", "time": 0, "duration": 2},
                    {"query": "abstract colorful texture", "time": 0, "duration": 2},
                    {"query": "modern city bokeh lights", "time": 0, "duration": 2},
                ])[: max(broll_max + 3, 6)]

            for i, cue in enumerate(cues[: max(broll_max, 5)]):
                query = str(cue.get("query") or "").strip()
                if not query:
                    continue
                try:
                    photos = await self.assets.search_photos(query, per_page=3)
                    if not photos:
                        continue
                    # Prefer different photo ids across inserts
                    pick = photos[min(i % len(photos), len(photos) - 1)]
                    local = await self.assets.download_photo(pick["url"], pick["id"])
                    if not local:
                        continue
                    photo_paths.append(local)
                    inserts.append({
                        "path": local,
                        "time": float(cue.get("time") or (1.2 + i * max(1.8, duration / max(broll_max, 1)))),
                        "duration": float(cue.get("duration") or (1.4 if not source_dark else 2.0)),
                        "opacity": 0.5 if not source_dark else 0.85,
                        "query": query,
                    })
                except Exception:
                    continue

            if no_speech:
                generation.api_responses["visual_mode"] = "silent_source"
            elif (source_dark or not inserts) and photo_paths:
                bg_path = os.path.join(work_dir, "photo_bg.mp4")
                try:
                    audio_for_bg = generation.audio_path or framed_path
                    self.ffmpeg.build_photo_background(
                        photo_paths[:6],
                        bg_path,
                        duration=duration,
                        width=width,
                        height=height,
                        audio_path=audio_for_bg if os.path.isfile(str(audio_for_bg)) else None,
                    )
                    framed_path = bg_path
                    generation.api_responses["visual_mode"] = "photo_background"
                    source_dark = True  # treat as replaced canvas for later accents
                except Exception as bg_err:
                    generation.api_responses["visual_mode"] = f"dark_fallback:{bg_err}"[:200]
            else:
                generation.api_responses["visual_mode"] = "talking_head"

            # 5) Zooms
            await self._update(generation, GenerationStatus.VIDEO_PROCESSING, progress=68)
            zoomed_path = os.path.join(work_dir, "zoomed.mp4")
            self.ffmpeg.apply_zoom_moments(
                framed_path,
                zoomed_path,
                zooms,
                max_zooms=int(style_cfg.get("zoom_cap") or 5),
                max_scale=float(style_cfg.get("zoom_scale_max") or 1.25),
            )

            # 6) Timed B-roll accents — only when there is speech meaning to illustrate
            broll_path = os.path.join(work_dir, "broll.mp4")
            if (
                inserts
                and not no_speech
                and generation.api_responses.get("visual_mode") == "talking_head"
            ):
                self.ffmpeg.overlay_broll_images(
                    zoomed_path,
                    broll_path,
                    inserts[:broll_max],
                    width=width,
                    height=height,
                    mode="pip",
                )
            else:
                shutil.copy2(zoomed_path, broll_path)
            generation.api_responses["broll_count"] = 0 if no_speech else len(inserts)

            # 7) Font + karaoke + hook
            font_family = "Arial"
            fonts_dir = None
            chosen_font_id = str(generation.api_responses.get("font_id") or "").strip()
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

            hook_text = None
            if hook and hook.get("use") and hook.get("text"):
                hook_text = str(hook["text"]).strip()[:90]
                generation.api_responses["hook_text"] = hook_text

            ass_path = os.path.join(work_dir, "karaoke.ass")
            self.ffmpeg.write_karaoke_ass(
                words=words,
                output_path=ass_path,
                highlight_words=highlights,
                font_name=font_family,
                video_width=width,
                video_height=height,
                hook_text=hook_text,
                hook_duration=float(style_cfg.get("hook_duration") or 2.8),
                font_size=int(style_cfg.get("font_size") or 64),
                hot_font_size=int(style_cfg.get("hot_font_size") or 72),
                words_per_chunk=int(style_cfg.get("words_per_chunk") or 4),
                caption_pop=bool(style_cfg.get("caption_pop")),
                outline=int(style_cfg.get("outline") or 3),
            )

            subtitled = os.path.join(work_dir, "subtitled.mp4")
            try:
                self.ffmpeg.burn_ass_subtitles(
                    broll_path, ass_path, subtitled, fonts_dir=fonts_dir
                )
            except Exception:
                shutil.copy2(broll_path, subtitled)

            # 8) Stickers (skip on silent — no topic to match, looks random)
            stickered = os.path.join(work_dir, "stickered.mp4")
            sticker_limit = int(style_cfg.get("sticker_limit") or 0)
            if (
                not no_speech
                and settings.STICKERS_ENABLED
                and style_cfg.get("stickers")
                and sticker_limit > 0
            ):
                try:
                    picked = await self.assets.auto_pick_for_video(text)
                    stickers = (picked.get("stickers") or [])[:sticker_limit]
                    if stickers:
                        self.ffmpeg.overlay_stickers(
                            subtitled, stickered, stickers=stickers
                        )
                    else:
                        shutil.copy2(subtitled, stickered)
                    generation.api_responses["assets"] = {
                        "theme": picked.get("theme"),
                        "stickers": len(stickers),
                    }
                except Exception:
                    shutil.copy2(subtitled, stickered)
            else:
                shutil.copy2(subtitled, stickered)
                if no_speech:
                    generation.api_responses["assets"] = {"stickers": 0, "skipped": "silent"}

            # 8) Background music + ducking
            scored_path = os.path.join(work_dir, "with_music.mp4")
            music_path = await self.music.ensure_mood_track(mood)
            music_vol = float(style_cfg.get("music_vol") or 0.14)
            if music_path:
                self.ffmpeg.mix_background_music(
                    stickered, music_path, scored_path, music_volume=music_vol
                )
                generation.api_responses["music"] = {"mood": mood, "path": os.path.basename(music_path)}
            else:
                shutil.copy2(stickered, scored_path)
                generation.api_responses["music"] = {"mood": mood, "path": None}

            # 9) Virality score
            await self._update(generation, GenerationStatus.VIDEO_PROCESSING, progress=92)
            try:
                virality = await self.llm.score_virality(
                    transcript=text,
                    duration=duration,
                    language=plan_language,
                    style=edit_style,
                    has_hook=bool(hook_text),
                    broll_count=len(inserts),
                    zoom_count=len(zooms),
                )
            except Exception as score_err:
                virality = {
                    "score": 60,
                    "summary": "Оценка недоступна",
                    "tips": [],
                    "error": str(score_err)[:200],
                }
            generation.api_responses["virality"] = virality

            # 10) Plan-based watermark (Basic/Standard platform mark, Premium clean/brand)
            marked_path = os.path.join(work_dir, "watermarked.mp4")
            wm = resolve_export_watermark(client)
            if wm and wm.get("path"):
                self.ffmpeg.apply_watermark(
                    scored_path,
                    marked_path,
                    watermark_path=wm["path"],
                    watermark_position=wm.get("position") or "bottom_right",
                    watermark_opacity=int(wm.get("opacity") or 70),
                    watermark_scale=int(wm.get("scale") or 13),
                )
                generation.api_responses["watermark"] = {
                    "kind": wm.get("kind"),
                    "applied": True,
                }
            else:
                shutil.copy2(scored_path, marked_path)
                generation.api_responses["watermark"] = {
                    "kind": None,
                    "applied": False,
                    "reason": "premium_clean",
                }

            final_path = os.path.join(work_dir, f"final_{generation_id}.mp4")
            shutil.copy2(marked_path, final_path)

            generation.final_video_path = final_path
            generation.duration_seconds = int(self.ffmpeg.get_video_duration(final_path))
            generation.file_size_bytes = self.ffmpeg.get_file_size(final_path)
            generation.progress_percent = 100
            generation.status = GenerationStatus.COMPLETED
            generation.completed_at = datetime.utcnow()

            if not generation.credit_deducted:
                client.credits_remaining -= 1
                client.credits_used_this_month += 1
                generation.credit_deducted = True

            await self.db.commit()
            return final_path

        except Exception as e:
            await self._fail(generation, str(e)[:500])
            return None

    @staticmethod
    def _format_size(fmt: str) -> tuple:
        mapping = {
            "9:16": (1080, 1920),
            "1:1": (1080, 1080),
            "16:9": (1920, 1080),
        }
        return mapping.get(fmt, (1080, 1920))

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
