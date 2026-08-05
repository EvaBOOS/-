"""Background music beds for viral edits (cached locally)."""
from __future__ import annotations

import os
import random
import subprocess
from typing import Dict, List, Optional

import httpx

from app.core.config import settings
from app.services.video.ffmpeg_service import FFmpegService

# Jamendo tag search per mood — used only when JAMENDO_CLIENT_ID is set.
MOOD_TO_JAMENDO_TAGS: Dict[str, str] = {
    "calm": "calm+ambient+relaxing",
    "energetic": "energetic+upbeat+driving",
    "motivational": "inspiring+uplifting+corporate",
    "dramatic": "dramatic+cinematic+dark",
}

# Real royalty-free tracks from Mixkit (https://mixkit.co/free-stock-music/ —
# "Mixkit License": free for commercial use, no attribution required, no
# login/paywall gate on the files themselves), picked from Mixkit's OWN mood
# categories (/free-stock-music/mood/<mood>/ — "motivational" maps to
# Mixkit's "motivating"), not guessed from genre. URLs and file sizes were
# verified reachable (HTTP 200, audio/mpeg) as of 2026-08-06; if Mixkit ever
# reshuffles catalog IDs, the download step below fails soft and falls back
# to the next track in the list, then to the procedural pad — never a crash.
# Several tracks per mood so repeat generations aren't always the same song.
MOOD_TRACKS: Dict[str, List[Dict[str, str]]] = {
    "calm": [
        {"id": "mixkit-443", "url": "https://assets.mixkit.co/music/443/443.mp3"},  # Serene View
        {"id": "mixkit-127", "url": "https://assets.mixkit.co/music/127/127.mp3"},  # Valley Sunset
        {"id": "mixkit-749", "url": "https://assets.mixkit.co/music/749/749.mp3"},  # Relaxation 05
    ],
    "energetic": [
        {"id": "mixkit-51", "url": "https://assets.mixkit.co/music/51/51.mp3"},  # Sports Highlights
        {"id": "mixkit-1068", "url": "https://assets.mixkit.co/music/1068/1068.mp3"},  # K.O.
        {"id": "mixkit-80", "url": "https://assets.mixkit.co/music/80/80.mp3"},  # Daredevil
    ],
    "motivational": [
        {"id": "mixkit-953", "url": "https://assets.mixkit.co/music/953/953.mp3"},  # Feel Alive
        {"id": "mixkit-1000", "url": "https://assets.mixkit.co/music/1000/1000.mp3"},  # I Can Hear Your Heartbeat
        {"id": "mixkit-1183", "url": "https://assets.mixkit.co/music/1183/1183.mp3"},  # Karma
    ],
    "dramatic": [
        {"id": "mixkit-614", "url": "https://assets.mixkit.co/music/614/614.mp3"},  # Silent Descent
        {"id": "mixkit-676", "url": "https://assets.mixkit.co/music/676/676.mp3"},  # Epical Drums 01
        {"id": "mixkit-601", "url": "https://assets.mixkit.co/music/601/601.mp3"},  # Skyline
    ],
}


class MusicLibraryService:
    def __init__(self) -> None:
        self.music_dir = os.path.join(settings.ASSETS_DIR, "cache", "music")
        os.makedirs(self.music_dir, exist_ok=True)
        self.ffmpeg = FFmpegService()

    async def ensure_mood_track(self, mood: str) -> Optional[str]:
        mood = (mood or "energetic").lower()

        # Live CC-licensed pick first, for real variety across generations —
        # entirely skipped (returns None immediately) without a configured
        # JAMENDO_CLIENT_ID, so this never blocks the fixed-catalog fallback.
        jamendo_path = await self._jamendo_pick(mood)
        if jamendo_path:
            return jamendo_path

        tracks = list(MOOD_TRACKS.get(mood) or MOOD_TRACKS["energetic"])
        random.shuffle(tracks)
        for meta in tracks:
            dest = os.path.join(self.music_dir, f"{meta['id']}.mp3")
            if os.path.isfile(dest) and os.path.getsize(dest) > 1000:
                return dest
            downloaded = await self._download(meta["url"], dest)
            if downloaded:
                return downloaded

        generated = os.path.join(self.music_dir, f"{mood}-pad.mp3")
        if os.path.isfile(generated) and os.path.getsize(generated) > 1000:
            return generated
        return self._generate_pad(generated, mood)

    async def _jamendo_pick(self, mood: str) -> Optional[str]:
        """
        Search Jamendo for a CC-licensed track matching the mood and download
        a random pick among the commercial-safe results — cached by track id,
        so a repeat pick is instant and a new generation can still land on a
        different (already-cached) track for variety.
        """
        if not settings.JAMENDO_CLIENT_ID:
            return None
        tags = MOOD_TO_JAMENDO_TAGS.get(mood, "background+instrumental")
        params = {
            "client_id": settings.JAMENDO_CLIENT_ID,
            "format": "json",
            "limit": 8,
            "tags": tags,
            "audioformat": "mp32",
            "include": "musicinfo",
            "order": "popularity_total",
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(
                    "https://api.jamendo.com/v3.0/tracks/", params=params
                )
                if resp.status_code != 200:
                    return None
                data = resp.json()
        except Exception:
            return None

        candidates = []
        for track in data.get("results") or []:
            # Only public-domain / attribution-only licenses — NonCommercial
            # (-nc) and NoDerivatives (-nd) are excluded entirely, since this
            # platform (a) sells the output to paying clients and (b) mixes
            # /ducks the track under their voice, which is a derivative use.
            lic = str(track.get("license_ccurl") or "").lower()
            url = track.get("audio")
            track_id = track.get("id")
            if not lic or not url or not track_id:
                continue
            if "-nc" in lic or "-nd" in lic:
                continue
            candidates.append((str(track_id), url))

        if not candidates:
            return None

        track_id, url = random.choice(candidates)
        dest = os.path.join(self.music_dir, f"jamendo-{track_id}.mp3")
        if os.path.isfile(dest) and os.path.getsize(dest) > 1000:
            return dest
        return await self._download(url, dest)

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
