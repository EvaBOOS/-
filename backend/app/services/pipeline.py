import os
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models.client import Client
from app.models.generation import VideoGeneration, GenerationStatus
from app.services.ai.openai_service import OpenAIService
from app.services.ai.elevenlabs_service import ElevenLabsService
from app.services.ai.edge_tts_service import EdgeTTSService
from app.services.ai.heygen_service import HeyGenService
from app.services.video.ffmpeg_service import FFmpegService
from app.services.assets.library_service import AssetLibraryService
from app.services.branding_watermark import resolve_export_watermark


class VideoGenerationPipeline:
    """
    Orchestrates the complete video generation pipeline:
    1. LLM (AITUNNEL / OpenAI): Generate viral script from user text
    2. Voice: ElevenLabs if key set, else Edge TTS (free, no geo block)
    3. HeyGen: Create avatar video with lip sync
    4. FFmpeg: Add subtitles, watermark, auto stickers/fonts from asset library
    """
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.openai = OpenAIService()  # AITUNNEL-first LLM wrapper
        self.elevenlabs = ElevenLabsService()
        self.edge_tts = EdgeTTSService()
        self.heygen = HeyGenService()
        self.ffmpeg = FFmpegService()
        self.assets = AssetLibraryService()
        self.use_elevenlabs = bool(settings.ELEVENLABS_API_KEY)
        
    async def process(self, generation_id: int, client_id: int) -> Optional[str]:
        """
        Process a video generation request through the complete pipeline.
        
        Args:
            generation_id: ID of the VideoGeneration record
            client_id: ID of the client
            
        Returns:
            Path to final video file or None if failed
        """
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
            await self._update_status(
                generation, 
                GenerationStatus.FAILED, 
                "Client not found"
            )
            return None
        
        generation.started_at = datetime.utcnow()
        await self.db.commit()
        
        generation_dir = os.path.join(
            settings.GENERATED_DIR,
            "video",
            str(client_id),
            str(generation_id)
        )
        os.makedirs(generation_dir, exist_ok=True)
        
        try:
            await self._update_status(generation, GenerationStatus.SCRIPT_GENERATION, progress=10)
            
            generated_script = await self.openai.generate_viral_script(
                generation.original_text,
                generation.target_language
            )
            
            generation.generated_script = generated_script
            generation.progress_percent = 20
            await self.db.commit()
            
            await self._update_status(generation, GenerationStatus.VOICE_SYNTHESIS, progress=25)
            
            audio_dir = os.path.join(settings.GENERATED_DIR, "audio", str(client_id))
            os.makedirs(audio_dir, exist_ok=True)
            
            if self.use_elevenlabs:
                voice_id = (
                    client.custom_voice_clone_id
                    or client.elevenlabs_voice_id
                    or "21m00Tcm4TlvDq8ikWAM"
                )
                audio_path = await self.elevenlabs.synthesize_speech(
                    text=generated_script,
                    voice_id=voice_id,
                    output_dir=audio_dir,
                )
            else:
                voice_id = client.elevenlabs_voice_id or settings.EDGE_TTS_VOICE
                audio_path = await self.edge_tts.synthesize_speech(
                    text=generated_script,
                    voice_id=voice_id,
                    output_dir=audio_dir,
                    language=generation.target_language,
                )
            
            generation.audio_path = audio_path
            generation.progress_percent = 40
            await self.db.commit()
            
            await self._update_status(generation, GenerationStatus.AVATAR_GENERATION, progress=45)
            
            avatar_id = client.heygen_avatar_id or settings.HEYGEN_AVATAR_ID
            
            avatar_video_path = await self._generate_avatar_video(
                script=generated_script,
                avatar_id=avatar_id,
                audio_path=audio_path,
                output_dir=generation_dir
            )
            
            generation.avatar_video_path = avatar_video_path
            generation.progress_percent = 75
            await self.db.commit()
            
            await self._update_status(generation, GenerationStatus.VIDEO_PROCESSING, progress=80)
            
            final_video_path = os.path.join(generation_dir, f"final_{generation_id}.mp4")
            branding = client.branding

            try:
                audio_duration = self.ffmpeg.get_audio_duration(audio_path)

                # Auto library: stickers + cool font by topic (no manual downloads)
                topic_blob = f"{generation.original_text or ''}\n{generated_script or ''}"
                picked = await self.assets.auto_pick_for_video(topic_blob)
                generation.api_responses = generation.api_responses or {}
                generation.api_responses["assets"] = {
                    "theme": picked.get("theme"),
                    "font_id": picked.get("font_id"),
                    "font_family": picked.get("font_family"),
                    "stickers": len(picked.get("stickers") or []),
                    "photo": bool(picked.get("photo_path")),
                }
                await self.db.commit()

                srt_path = os.path.join(generation_dir, "subtitles.srt")
                self.ffmpeg.generate_srt_from_text(
                    text=generated_script,
                    duration_seconds=audio_duration,
                    output_path=srt_path
                )

                has_custom_font = bool(
                    branding
                    and branding.subtitle_font_path
                    and os.path.isfile(branding.subtitle_font_path)
                )
                subtitle_font = (
                    branding.subtitle_font_name if branding and branding.subtitle_font_name
                    else "Arial"
                )
                subtitle_font_path = branding.subtitle_font_path if branding else None
                subtitle_fonts_dir = None
                if (
                    settings.AUTO_FONT_ENABLED
                    and not has_custom_font
                    and picked.get("font_family")
                ):
                    subtitle_font = picked["font_family"]
                    subtitle_fonts_dir = picked.get("fonts_dir")
                    subtitle_font_path = None

                wm = resolve_export_watermark(client)
                wm_path = wm["path"] if wm else None
                wm_pos = (wm or {}).get("position") or "bottom_right"
                wm_opacity = int((wm or {}).get("opacity") or 80)
                wm_scale = int((wm or {}).get("scale") or 15)
                generation.api_responses = generation.api_responses or {}
                generation.api_responses["watermark"] = {
                    "kind": (wm or {}).get("kind"),
                    "applied": bool(wm_path),
                }

                self.ffmpeg.add_subtitles_and_watermark(
                    video_path=avatar_video_path,
                    output_path=final_video_path,
                    subtitle_path=srt_path,
                    watermark_path=wm_path,
                    watermark_position=wm_pos,
                    watermark_opacity=wm_opacity,
                    watermark_scale=wm_scale,
                    subtitle_font=subtitle_font,
                    subtitle_font_path=subtitle_font_path,
                    subtitle_fonts_dir=subtitle_fonts_dir,
                    subtitle_font_size=branding.subtitle_font_size if branding else 42,
                    subtitle_font_color=branding.subtitle_font_color if branding else "white",
                    subtitle_bg_color=branding.subtitle_bg_color if branding else None,
                    subtitle_position=branding.subtitle_position if branding else "bottom"
                )

                if settings.STICKERS_ENABLED:
                    stickers = list(picked.get("stickers") or [])
                    # Themed Pexels photo as a soft plate behind emoji stickers
                    if picked.get("photo_path") and os.path.isfile(picked["photo_path"]):
                        stickers.insert(0, {
                            "path": picked["photo_path"],
                            "position": "top_right",
                            "scale": 28,
                            "opacity": 55,
                        })
                    if stickers:
                        stickered = os.path.join(
                            generation_dir, f"final_{generation_id}_stickers.mp4"
                        )
                        self.ffmpeg.overlay_stickers(
                            video_path=final_video_path,
                            output_path=stickered,
                            stickers=stickers,
                        )
                        if os.path.isfile(stickered):
                            os.replace(stickered, final_video_path)
                    else:
                        # Fallback: local Typiq pack if present
                        stickers_dir = os.path.join(settings.ASSETS_DIR, "stickers")
                        if os.path.isdir(stickers_dir):
                            stickered = os.path.join(
                                generation_dir, f"final_{generation_id}_stickers.mp4"
                            )
                            self.ffmpeg.overlay_stickers(
                                video_path=final_video_path,
                                output_path=stickered,
                                stickers_dir=stickers_dir,
                            )
                            if os.path.isfile(stickered):
                                os.replace(stickered, final_video_path)
            except FileNotFoundError:
                # FFmpeg/ffprobe missing — deliver HeyGen video as-is
                import shutil
                shutil.copy2(avatar_video_path, final_video_path)
                audio_duration = 0
            except Exception as post_err:
                # Soft-fail post-processing so a successful avatar is not discarded
                import shutil
                if os.path.exists(avatar_video_path):
                    shutil.copy2(avatar_video_path, final_video_path)
                    audio_duration = 0
                else:
                    raise post_err
            
            generation.final_video_path = final_video_path
            generation.duration_seconds = int(audio_duration)
            generation.file_size_bytes = self.ffmpeg.get_file_size(final_video_path)
            generation.progress_percent = 100
            generation.status = GenerationStatus.COMPLETED
            generation.completed_at = datetime.utcnow()
            
            if not generation.credit_deducted:
                client.credits_remaining -= 1
                client.credits_used_this_month += 1
                generation.credit_deducted = True
            
            await self.db.commit()
            
            return final_video_path
            
        except Exception as e:
            error_msg = str(e)
            await self._update_status(
                generation,
                GenerationStatus.FAILED,
                error_msg[:500]
            )
            return None
    
    async def _generate_avatar_video(
        self,
        script: str,
        avatar_id: str,
        audio_path: str,
        output_dir: str
    ) -> str:
        """
        Lip-sync avatar to our Edge/ElevenLabs audio (preferred).
        Falls back to HeyGen TTS with a real HeyGen voice_id.
        """
        errors = []

        try:
            return await self.heygen.create_video_from_local_audio(
                audio_path=audio_path,
                avatar_id=avatar_id,
                output_dir=output_dir,
            )
        except Exception as e:
            errors.append(f"audio lipsync: {e}")

        try:
            return await self.heygen.create_video_from_script(
                script=script,
                avatar_id=avatar_id,
                voice_id=settings.HEYGEN_VOICE_ID,
                output_dir=output_dir,
            )
        except Exception as e:
            errors.append(f"script TTS: {e}")
            raise Exception(
                "Avatar video generation failed: " + " | ".join(errors)
            )
    
    async def _update_status(
        self,
        generation: VideoGeneration,
        status: GenerationStatus,
        error: str = None,
        progress: int = None
    ):
        """Update generation status in database."""
        generation.status = status
        if error:
            generation.error_message = error
        if progress is not None:
            generation.progress_percent = progress
        
        generation.api_responses = generation.api_responses or {}
        generation.api_responses[status.value] = {
            "timestamp": datetime.utcnow().isoformat(),
            "error": error
        }
        
        await self.db.commit()


class MockVideoGenerationPipeline(VideoGenerationPipeline):
    """
    Mock pipeline for testing without actual API calls.
    Generates placeholder videos for development/testing.
    """
    
    async def process(self, generation_id: int, client_id: int) -> Optional[str]:
        """Process with mock data."""
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
            await self._update_status(
                generation, 
                GenerationStatus.FAILED, 
                "Client not found"
            )
            return None
        
        generation.started_at = datetime.utcnow()
        
        try:
            import asyncio
            
            await self._update_status(generation, GenerationStatus.SCRIPT_GENERATION, progress=10)
            await asyncio.sleep(1)
            
            mock_script = f"""[MOCK GENERATED SCRIPT]

Hook: Did you know this one simple trick can change everything?

{generation.original_text}

Call to Action: Like and follow for more tips!"""
            
            generation.generated_script = mock_script
            generation.progress_percent = 30
            await self.db.commit()
            
            await self._update_status(generation, GenerationStatus.VOICE_SYNTHESIS, progress=40)
            await asyncio.sleep(1)
            
            await self._update_status(generation, GenerationStatus.AVATAR_GENERATION, progress=60)
            await asyncio.sleep(2)
            
            await self._update_status(generation, GenerationStatus.VIDEO_PROCESSING, progress=80)
            await asyncio.sleep(1)
            
            generation_dir = os.path.join(
                settings.GENERATED_DIR,
                "video",
                str(client_id),
                str(generation_id)
            )
            os.makedirs(generation_dir, exist_ok=True)
            
            mock_video_path = os.path.join(generation_dir, f"mock_{generation_id}.mp4")
            
            self._create_mock_video(mock_video_path)
            
            generation.final_video_path = mock_video_path
            generation.duration_seconds = 30
            generation.file_size_bytes = os.path.getsize(mock_video_path) if os.path.exists(mock_video_path) else 0
            generation.progress_percent = 100
            generation.status = GenerationStatus.COMPLETED
            generation.completed_at = datetime.utcnow()
            
            if not generation.credit_deducted:
                client.credits_remaining -= 1
                client.credits_used_this_month += 1
                generation.credit_deducted = True
            
            await self.db.commit()
            
            return mock_video_path
            
        except Exception as e:
            await self._update_status(
                generation,
                GenerationStatus.FAILED,
                str(e)[:500]
            )
            return None
    
    def _create_mock_video(self, output_path: str):
        """Create a simple test video with FFmpeg."""
        import subprocess
        
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "color=c=blue:s=1080x1920:d=5",
            "-f", "lavfi",
            "-i", "anullsrc=r=44100:cl=stereo",
            "-t", "5",
            "-vf", "drawtext=text='MOCK VIDEO':fontsize=72:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2",
            "-c:v", "libx264",
            "-c:a", "aac",
            "-shortest",
            output_path
        ]
        
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True)
        except Exception:
            with open(output_path, 'wb') as f:
                f.write(b'\x00' * 1000)
