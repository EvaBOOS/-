import os
import uuid
from typing import Optional

import edge_tts

from app.core.config import settings


# Sensible defaults by language code prefix
_LANG_VOICE_MAP = {
    "ru": "ru-RU-SvetlanaNeural",
    "en": "en-US-JennyNeural",
    "es": "es-ES-ElviraNeural",
    "de": "de-DE-KatjaNeural",
    "fr": "fr-FR-DeniseNeural",
    "it": "it-IT-ElsaNeural",
    "pt": "pt-BR-FranciscaNeural",
    "uk": "uk-UA-PolinaNeural",
    "pl": "pl-PL-ZofiaNeural",
    "tr": "tr-TR-EmelNeural",
    "ar": "ar-SA-ZariyahNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
    "ja": "ja-JP-NanamiNeural",
    "ko": "ko-KR-SunHiNeural",
}


class EdgeTTSService:
    """Free Microsoft Edge TTS — no API key, works from most regions."""

    def __init__(self):
        self.default_voice = settings.EDGE_TTS_VOICE

    def resolve_voice(self, voice_id: Optional[str] = None, language: Optional[str] = None) -> str:
        """
        Pick an Edge voice.
        Ignores ElevenLabs-style IDs (short hashes) and falls back by language.
        """
        if voice_id and "Neural" in voice_id:
            return voice_id

        if language:
            prefix = language.lower().split("-")[0]
            if prefix in _LANG_VOICE_MAP:
                return _LANG_VOICE_MAP[prefix]

        return self.default_voice

    async def synthesize_speech(
        self,
        text: str,
        voice_id: str,
        output_dir: str,
        language: Optional[str] = None,
        **_kwargs,
    ) -> str:
        """Generate MP3 speech via edge-tts. Extra kwargs ignored for API parity."""
        os.makedirs(output_dir, exist_ok=True)

        voice = self.resolve_voice(voice_id, language)
        filename = f"{uuid.uuid4()}.mp3"
        filepath = os.path.join(output_dir, filename)

        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(filepath)

        return filepath

    async def list_voices(self, locale_prefix: Optional[str] = None) -> list:
        voices = await edge_tts.list_voices()
        if locale_prefix:
            prefix = locale_prefix.lower()
            voices = [v for v in voices if v.get("Locale", "").lower().startswith(prefix)]
        return voices
