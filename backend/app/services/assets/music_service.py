"""Background music beds for viral edits (cached locally)."""
from __future__ import annotations

import os
import subprocess
from typing import Dict, Optional

import httpx

from app.core.config import settings
from app.services.video.ffmpeg_service import FFmpegService

# Prefer remote CC beds; fall back to generated soft pad via FFmpeg.
MOOD_TRACKS: Dict[str, Dict[str, str]] = {
    "calm": {
        "id": "calm-ambient",
        "url": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-8.mp3",
    },
    "energetic": {
        "id": "energetic-drive",
        "url": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3",
    },
    "motivational": {
        "id": "motivational-rise",
        "url": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-2.mp3",
    },
    "dramatic": {
        "id": "dramatic-deep",
        "url": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-10.mp3",
    },
}


class MusicLibraryService:
    def __init__(self) -> None:
        self.music_dir = os.path.join(settings.ASSETS_DIR, "cache", "music")
        os.makedirs(self.music_dir, exist_ok=True)
        self.ffmpeg = FFmpegService()

    async def ensure_mood_track(self, mood: str) -> Optional[str]:
        mood = (mood or "energetic").lower()
        meta = MOOD_TRACKS.get(mood) or MOOD_TRACKS["energetic"]
        dest = os.path.join(self.music_dir, f"{meta['id']}.mp3")
        if os.path.isfile(dest) and os.path.getsize(dest) > 1000:
            return dest

        downloaded = await self._download(meta["url"], dest)
        if downloaded:
            return downloaded

        generated = os.path.join(self.music_dir, f"{meta['id']}-pad.mp3")
        if os.path.isfile(generated) and os.path.getsize(generated) > 1000:
            return generated
        return self._generate_pad(generated, mood)

    async def _download(self, url: str, dest: str) -> Optional[str]:
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                resp = await client.get(url)
                if resp.status_code != 200 or len(resp.content) < 1000:
                    return None
                with open(dest, "wb") as f:
                    f.write(resp.content)
            return dest
        except Exception:
            return None

    def _generate_pad(self, dest: str, mood: str) -> Optional[str]:
        """Soft procedural bed if remote music is unavailable."""
        freq = {
            "calm": 110,
            "energetic": 165,
            "motivational": 147,
            "dramatic": 98,
        }.get(mood, 130)
        amp = 0.035 if mood == "minimal" else 0.05
        cmd = [
            self.ffmpeg.ffmpeg_bin, "-y",
            "-f", "lavfi",
            "-i", f"sine=frequency={freq}:sample_rate=44100:duration=45",
            "-f", "lavfi",
            "-i", f"anoisesrc=color=pink:amplitude={amp}:sample_rate=44100:duration=45",
            "-filter_complex",
            f"[0:a]volume=0.12,lowpass=f=600[a0];"
            f"[1:a]lowpass=f=500,volume=0.35[a1];"
            f"[a0][a1]amix=inputs=2:duration=first,afade=t=in:st=0:d=1.5,afade=t=out:st=42:d=3[a]",
            "-map", "[a]",
            "-c:a", "libmp3lame", "-q:a", "5",
            dest,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not os.path.isfile(dest):
            return None
        return dest
