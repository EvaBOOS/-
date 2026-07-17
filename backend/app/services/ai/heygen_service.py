import httpx
import os
import uuid
import asyncio
from typing import Optional
from app.core.config import settings


class HeyGenService:
    """Service for HeyGen avatar video generation API integration."""
    
    def __init__(self):
        self.api_key = settings.HEYGEN_API_KEY
        self.base_url = "https://api.heygen.com/v2"
        self.v1_base_url = "https://api.heygen.com/v1"
        
    async def list_avatars(self) -> list:
        """Get list of available avatars."""
        if not self.api_key:
            raise ValueError("HeyGen API key not configured")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.v1_base_url}/avatars",
                headers={"X-Api-Key": self.api_key}
            )
            
            if response.status_code != 200:
                raise Exception(f"HeyGen API error: {response.text}")
            
            data = response.json()
            return data.get("data", {}).get("avatars", [])
    
    async def create_video_from_audio(
        self,
        audio_url: str,
        avatar_id: str,
        output_dir: str,
        aspect_ratio: str = "9:16",
        test_mode: bool = False
    ) -> str:
        """
        Create avatar video with lip sync from audio file.
        
        Args:
            audio_url: URL to the audio file (must be publicly accessible)
            avatar_id: HeyGen avatar ID
            output_dir: Directory to save the video
            aspect_ratio: Video aspect ratio (9:16 for vertical shorts)
            test_mode: If True, use test API endpoint
            
        Returns:
            Path to generated video file
        """
        if not self.api_key:
            raise ValueError("HeyGen API key not configured")
        
        os.makedirs(output_dir, exist_ok=True)
        
        async with httpx.AsyncClient(timeout=300.0) as client:
            create_response = await client.post(
                f"{self.base_url}/video/generate",
                headers={
                    "X-Api-Key": self.api_key,
                    "Content-Type": "application/json"
                },
                json={
                    "video_inputs": [{
                        "character": {
                            "type": "avatar",
                            "avatar_id": avatar_id,
                            "avatar_style": "normal"
                        },
                        "voice": {
                            "type": "audio",
                            "audio_url": audio_url
                        }
                    }],
                    "dimension": {
                        "width": 1080,
                        "height": 1920
                    } if aspect_ratio == "9:16" else {
                        "width": 1920,
                        "height": 1080
                    },
                    "test": test_mode
                }
            )
            
            if create_response.status_code != 200:
                raise Exception(f"HeyGen API error: {create_response.text}")
            
            create_data = create_response.json()
            video_id = create_data.get("data", {}).get("video_id")
            
            if not video_id:
                raise Exception("Failed to get video ID from HeyGen")
            
            video_url = await self._poll_video_status(client, video_id)
            
            video_response = await client.get(video_url)
            if video_response.status_code != 200:
                raise Exception(f"Failed to download video: {video_response.status_code}")
            
            filename = f"{uuid.uuid4()}.mp4"
            filepath = os.path.join(output_dir, filename)
            
            with open(filepath, "wb") as f:
                f.write(video_response.content)
            
            return filepath
    
    async def create_video_from_script(
        self,
        script: str,
        avatar_id: str,
        voice_id: str,
        output_dir: str,
        aspect_ratio: str = "9:16"
    ) -> str:
        """
        Create avatar video directly from script using HeyGen's TTS.
        
        Args:
            script: Text script for the video
            avatar_id: HeyGen avatar ID
            voice_id: HeyGen voice ID
            output_dir: Directory to save the video
            aspect_ratio: Video aspect ratio
            
        Returns:
            Path to generated video file
        """
        if not self.api_key:
            raise ValueError("HeyGen API key not configured")
        
        os.makedirs(output_dir, exist_ok=True)
        
        async with httpx.AsyncClient(timeout=300.0) as client:
            create_response = await client.post(
                f"{self.base_url}/video/generate",
                headers={
                    "X-Api-Key": self.api_key,
                    "Content-Type": "application/json"
                },
                json={
                    "video_inputs": [{
                        "character": {
                            "type": "avatar",
                            "avatar_id": avatar_id,
                            "avatar_style": "normal"
                        },
                        "voice": {
                            "type": "text",
                            "input_text": script,
                            "voice_id": voice_id
                        }
                    }],
                    "dimension": {
                        "width": 1080,
                        "height": 1920
                    } if aspect_ratio == "9:16" else {
                        "width": 1920,
                        "height": 1080
                    }
                }
            )
            
            if create_response.status_code != 200:
                raise Exception(f"HeyGen API error: {create_response.text}")
            
            create_data = create_response.json()
            video_id = create_data.get("data", {}).get("video_id")
            
            if not video_id:
                raise Exception("Failed to get video ID from HeyGen")
            
            video_url = await self._poll_video_status(client, video_id)
            
            video_response = await client.get(video_url)
            if video_response.status_code != 200:
                raise Exception(f"Failed to download video: {video_response.status_code}")
            
            filename = f"{uuid.uuid4()}.mp4"
            filepath = os.path.join(output_dir, filename)
            
            with open(filepath, "wb") as f:
                f.write(video_response.content)
            
            return filepath
    
    async def _poll_video_status(
        self, 
        client: httpx.AsyncClient, 
        video_id: str,
        max_attempts: int = 60,
        poll_interval: int = 10
    ) -> str:
        """
        Poll HeyGen API for video generation status.
        
        Args:
            client: HTTP client
            video_id: Video ID to check
            max_attempts: Maximum polling attempts
            poll_interval: Seconds between polls
            
        Returns:
            URL of completed video
        """
        for attempt in range(max_attempts):
            status_response = await client.get(
                f"{self.v1_base_url}/video_status.get",
                params={"video_id": video_id},
                headers={"X-Api-Key": self.api_key}
            )
            
            if status_response.status_code != 200:
                raise Exception(f"HeyGen status check error: {status_response.text}")
            
            status_data = status_response.json()
            status = status_data.get("data", {}).get("status")
            
            if status == "completed":
                video_url = status_data.get("data", {}).get("video_url")
                if video_url:
                    return video_url
                raise Exception("Video completed but no URL returned")
            
            elif status == "failed":
                error = status_data.get("data", {}).get("error", "Unknown error")
                raise Exception(f"HeyGen video generation failed: {error}")
            
            elif status in ["pending", "processing"]:
                await asyncio.sleep(poll_interval)
            
            else:
                await asyncio.sleep(poll_interval)
        
        raise Exception(f"Video generation timed out after {max_attempts * poll_interval} seconds")
    
    async def get_avatar_info(self, avatar_id: str) -> dict:
        """Get information about a specific avatar."""
        if not self.api_key:
            raise ValueError("HeyGen API key not configured")
        
        avatars = await self.list_avatars()
        for avatar in avatars:
            if avatar.get("avatar_id") == avatar_id:
                return avatar
        
        raise Exception(f"Avatar {avatar_id} not found")
