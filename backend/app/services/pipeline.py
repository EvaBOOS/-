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
from app.services.ai.heygen_service import HeyGenService
from app.services.video.ffmpeg_service import FFmpegService


class VideoGenerationPipeline:
    """
    Orchestrates the complete video generation pipeline:
    1. GPT-4o: Generate viral script from user text
    2. ElevenLabs: Synthesize voice audio
    3. HeyGen: Create avatar video with lip sync
    4. FFmpeg: Add subtitles and watermark
    """
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.openai = OpenAIService()
        self.elevenlabs = ElevenLabsService()
        self.heygen = HeyGenService()
        self.ffmpeg = FFmpegService()
        
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
            
            voice_id = (
                client.custom_voice_clone_id or 
                client.elevenlabs_voice_id or 
                "21m00Tcm4TlvDq8ikWAM"
            )
            
            audio_dir = os.path.join(settings.GENERATED_DIR, "audio", str(client_id))
            os.makedirs(audio_dir, exist_ok=True)
            
            audio_path = await self.elevenlabs.synthesize_speech(
                text=generated_script,
                voice_id=voice_id,
                output_dir=audio_dir
            )
            
            generation.audio_path = audio_path
            generation.progress_percent = 40
            await self.db.commit()
            
            await self._update_status(generation, GenerationStatus.AVATAR_GENERATION, progress=45)
            
            avatar_id = client.heygen_avatar_id or "josh_lite3_20230714"
            
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
            
            audio_duration = self.ffmpeg.get_audio_duration(audio_path)
            
            srt_path = os.path.join(generation_dir, "subtitles.srt")
            self.ffmpeg.generate_srt_from_text(
                text=generated_script,
                duration_seconds=audio_duration,
                output_path=srt_path
            )
            
            final_video_path = os.path.join(generation_dir, f"final_{generation_id}.mp4")
            
            branding = client.branding
            
            self.ffmpeg.add_subtitles_and_watermark(
                video_path=avatar_video_path,
                output_path=final_video_path,
                subtitle_path=srt_path,
                watermark_path=branding.watermark_path if branding else None,
                watermark_position=branding.watermark_position if branding else "bottom_right",
                watermark_opacity=branding.watermark_opacity if branding else 80,
                watermark_scale=branding.watermark_scale if branding else 15,
                subtitle_font=branding.subtitle_font_name if branding else "Arial",
                subtitle_font_path=branding.subtitle_font_path if branding else None,
                subtitle_font_size=branding.subtitle_font_size if branding else 48,
                subtitle_font_color=branding.subtitle_font_color if branding else "white",
                subtitle_bg_color=branding.subtitle_bg_color if branding else None,
                subtitle_position=branding.subtitle_position if branding else "bottom"
            )
            
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
        Generate avatar video with lip sync.
        
        This method tries to use HeyGen with audio upload first,
        falling back to text-based generation if needed.
        """
        try:
            video_path = await self.heygen.create_video_from_script(
                script=script,
                avatar_id=avatar_id,
                voice_id="en-US-JennyNeural",
                output_dir=output_dir
            )
            
            final_path = os.path.join(output_dir, f"avatar_{uuid.uuid4()}.mp4")
            self.ffmpeg.combine_audio_video(
                video_path=video_path,
                audio_path=audio_path,
                output_path=final_path
            )
            
            return final_path
            
        except Exception as e:
            raise Exception(f"Avatar video generation failed: {str(e)}")
    
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
