import httpx
import os
import uuid
from typing import Optional
from app.core.config import settings


class ElevenLabsService:
    """Service for ElevenLabs voice synthesis API integration."""
    
    def __init__(self):
        self.api_key = settings.ELEVENLABS_API_KEY
        self.base_url = "https://api.elevenlabs.io/v1"
        
    async def list_voices(self) -> list:
        """Get list of available voices."""
        if not self.api_key:
            raise ValueError("ElevenLabs API key not configured")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.base_url}/voices",
                headers={"xi-api-key": self.api_key}
            )
            
            if response.status_code != 200:
                raise Exception(f"ElevenLabs API error: {response.text}")
            
            data = response.json()
            return data.get("voices", [])
    
    async def synthesize_speech(
        self,
        text: str,
        voice_id: str,
        output_dir: str,
        model_id: str = "eleven_multilingual_v2",
        stability: float = 0.5,
        similarity_boost: float = 0.75,
        style: float = 0.0,
        use_speaker_boost: bool = True
    ) -> str:
        """
        Generate speech audio from text using ElevenLabs API.
        
        Args:
            text: Text to convert to speech
            voice_id: ElevenLabs voice ID
            output_dir: Directory to save the audio file
            model_id: Model to use (eleven_multilingual_v2 recommended for non-English)
            stability: Voice stability (0-1)
            similarity_boost: Voice similarity enhancement (0-1)
            style: Style exaggeration (0-1)
            use_speaker_boost: Enable speaker boost
            
        Returns:
            Path to generated audio file
        """
        if not self.api_key:
            raise ValueError("ElevenLabs API key not configured")
        
        os.makedirs(output_dir, exist_ok=True)
        
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self.base_url}/text-to-speech/{voice_id}",
                headers={
                    "xi-api-key": self.api_key,
                    "Content-Type": "application/json",
                    "Accept": "audio/mpeg"
                },
                json={
                    "text": text,
                    "model_id": model_id,
                    "voice_settings": {
                        "stability": stability,
                        "similarity_boost": similarity_boost,
                        "style": style,
                        "use_speaker_boost": use_speaker_boost
                    }
                }
            )
            
            if response.status_code != 200:
                raise Exception(f"ElevenLabs API error: {response.text}")
            
            filename = f"{uuid.uuid4()}.mp3"
            filepath = os.path.join(output_dir, filename)
            
            with open(filepath, "wb") as f:
                f.write(response.content)
            
            return filepath
    
    async def synthesize_with_timestamps(
        self,
        text: str,
        voice_id: str,
        output_dir: str,
        model_id: str = "eleven_multilingual_v2"
    ) -> dict:
        """
        Generate speech with word-level timestamps for subtitle sync.
        
        Returns:
            Dict with 'audio_path' and 'alignment' (word timestamps)
        """
        if not self.api_key:
            raise ValueError("ElevenLabs API key not configured")
        
        os.makedirs(output_dir, exist_ok=True)
        
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self.base_url}/text-to-speech/{voice_id}/with-timestamps",
                headers={
                    "xi-api-key": self.api_key,
                    "Content-Type": "application/json"
                },
                json={
                    "text": text,
                    "model_id": model_id,
                    "voice_settings": {
                        "stability": 0.5,
                        "similarity_boost": 0.75
                    }
                }
            )
            
            if response.status_code != 200:
                raise Exception(f"ElevenLabs API error: {response.text}")
            
            data = response.json()
            
            audio_base64 = data.get("audio_base64")
            alignment = data.get("alignment", {})
            
            import base64
            audio_bytes = base64.b64decode(audio_base64)
            
            filename = f"{uuid.uuid4()}.mp3"
            filepath = os.path.join(output_dir, filename)
            
            with open(filepath, "wb") as f:
                f.write(audio_bytes)
            
            return {
                "audio_path": filepath,
                "alignment": alignment
            }
    
    async def get_voice_info(self, voice_id: str) -> dict:
        """Get information about a specific voice."""
        if not self.api_key:
            raise ValueError("ElevenLabs API key not configured")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.base_url}/voices/{voice_id}",
                headers={"xi-api-key": self.api_key}
            )
            
            if response.status_code != 200:
                raise Exception(f"ElevenLabs API error: {response.text}")
            
            return response.json()
