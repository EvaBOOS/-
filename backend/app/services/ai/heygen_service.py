import httpx
import os
import uuid
import asyncio
from typing import Optional
from app.core.config import settings


class HeyGenService:
    """HeyGen avatar video via API v3 (audio lipsync / script TTS)."""

    def __init__(self):
        self.api_key = settings.HEYGEN_API_KEY
        self.base_url = "https://api.heygen.com"
        self.v3 = f"{self.base_url}/v3"
        # Legacy list endpoints still useful for browsing public avatars/voices
        self.v2 = f"{self.base_url}/v2"
        self.v1 = f"{self.base_url}/v1"

    def _headers(self, json_body: bool = False) -> dict:
        headers = {"X-Api-Key": self.api_key}
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    async def list_avatars(self) -> list:
        """List public/studio avatars (v2 catalog)."""
        if not self.api_key:
            raise ValueError("HeyGen API key not configured")

        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.get(
                f"{self.v2}/avatars",
                headers=self._headers(),
            )
            if response.status_code != 200:
                raise Exception(f"HeyGen API error: {response.text}")
            return response.json().get("data", {}).get("avatars", [])

    async def list_voices(self) -> list:
        if not self.api_key:
            raise ValueError("HeyGen API key not configured")

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(
                f"{self.v2}/voices",
                headers=self._headers(),
            )
            if response.status_code != 200:
                raise Exception(f"HeyGen API error: {response.text}")
            return response.json().get("data", {}).get("voices", [])

    async def upload_audio(self, audio_path: str) -> dict:
        """Upload audio via POST /v3/assets; returns data with id (and optional url)."""
        if not self.api_key:
            raise ValueError("HeyGen API key not configured")

        filename = os.path.basename(audio_path)
        async with httpx.AsyncClient(timeout=120.0) as client:
            with open(audio_path, "rb") as f:
                response = await client.post(
                    f"{self.v3}/assets",
                    headers=self._headers(),
                    files={"file": (filename, f, "audio/mpeg")},
                )
            if response.status_code not in (200, 201):
                # Fallback: legacy binary upload
                with open(audio_path, "rb") as f:
                    audio_bytes = f.read()
                legacy = await client.post(
                    "https://upload.heygen.com/v1/asset",
                    headers={
                        "X-Api-Key": self.api_key,
                        "Content-Type": "audio/mpeg",
                    },
                    content=audio_bytes,
                )
                if legacy.status_code not in (200, 201):
                    raise Exception(
                        f"HeyGen upload error: v3={response.text}; legacy={legacy.text}"
                    )
                data = legacy.json().get("data") or {}
            else:
                data = response.json().get("data") or {}

            if not data.get("id") and not data.get("url"):
                raise Exception(f"HeyGen upload returned no asset: {data}")
            return data

    async def resolve_engine(self, avatar_id: str) -> dict:
        """
        Pick a compatible render engine for this look.
        Studio avatars often support only avatar_iii; photo avatars usually avatar_iv.
        """
        preference = ["avatar_iv", "avatar_iii", "avatar_v"]
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self.v3}/avatars/looks/{avatar_id}",
                    headers=self._headers(),
                )
            if response.status_code == 200:
                data = response.json().get("data") or {}
                supported = data.get("supported_api_engines") or []
                for engine in preference:
                    if engine in supported:
                        return {"type": engine}
                if supported:
                    return {"type": supported[0]}
        except Exception:
            pass
        # Safe default for many public studio looks (e.g. Abigail)
        return {"type": "avatar_iii"}

    async def create_video_from_local_audio(
        self,
        audio_path: str,
        avatar_id: str,
        output_dir: str,
        aspect_ratio: str = "9:16",
    ) -> str:
        """Upload local audio and lip-sync via POST /v3/videos."""
        asset = await self.upload_audio(audio_path)
        asset_id = asset.get("id") or asset.get("asset_id")
        audio_url = asset.get("url")
        engine = await self.resolve_engine(avatar_id)

        payload = {
            "type": "avatar",
            "avatar_id": avatar_id,
            "title": f"videogen-{uuid.uuid4().hex[:8]}",
            "aspect_ratio": aspect_ratio if aspect_ratio in ("9:16", "16:9", "1:1") else "9:16",
            "resolution": "1080p",
            "engine": engine,
            "background": {
                "type": "color",
                "value": settings.HEYGEN_BACKGROUND_COLOR,
            },
            "fit": "contain",
        }
        if asset_id:
            payload["audio_asset_id"] = asset_id
        elif audio_url:
            payload["audio_url"] = audio_url
        else:
            raise Exception("No audio_asset_id or audio_url after upload")

        return await self._create_and_download(payload, output_dir)

    async def create_video_from_script(
        self,
        script: str,
        avatar_id: str,
        voice_id: str,
        output_dir: str,
        aspect_ratio: str = "9:16",
    ) -> str:
        """Create video from text + HeyGen voice_id via POST /v3/videos."""
        if not self.api_key:
            raise ValueError("HeyGen API key not configured")

        engine = await self.resolve_engine(avatar_id)
        payload = {
            "type": "avatar",
            "avatar_id": avatar_id,
            "script": script,
            "voice_id": voice_id,
            "title": f"videogen-{uuid.uuid4().hex[:8]}",
            "aspect_ratio": aspect_ratio if aspect_ratio in ("9:16", "16:9", "1:1") else "9:16",
            "resolution": "1080p",
            "engine": engine,
            "background": {
                "type": "color",
                "value": settings.HEYGEN_BACKGROUND_COLOR,
            },
            "fit": "contain",
        }
        return await self._create_and_download(payload, output_dir)

    async def _create_and_download(self, payload: dict, output_dir: str) -> str:
        os.makedirs(output_dir, exist_ok=True)

        async with httpx.AsyncClient(timeout=300.0) as client:
            create_response = await client.post(
                f"{self.v3}/videos",
                headers=self._headers(json_body=True),
                json=payload,
            )
            if create_response.status_code not in (200, 201):
                raise Exception(f"HeyGen API error: {create_response.text}")

            create_data = create_response.json().get("data") or {}
            video_id = create_data.get("video_id") or create_data.get("id")
            if not video_id:
                raise Exception(f"Failed to get video ID from HeyGen: {create_response.text}")

            video_url = await self._poll_video_status(client, video_id)

            video_response = await client.get(video_url)
            if video_response.status_code != 200:
                raise Exception(f"Failed to download video: {video_response.status_code}")

            filepath = os.path.join(output_dir, f"{uuid.uuid4()}.mp4")
            with open(filepath, "wb") as f:
                f.write(video_response.content)
            return filepath

    async def _poll_video_status(
        self,
        client: httpx.AsyncClient,
        video_id: str,
        max_attempts: int = 90,
        poll_interval: int = 10,
    ) -> str:
        """Poll GET /v3/videos/{id}; fall back to legacy status endpoint."""
        for _ in range(max_attempts):
            # v3
            status_response = await client.get(
                f"{self.v3}/videos/{video_id}",
                headers=self._headers(),
            )
            if status_response.status_code == 200:
                data = status_response.json().get("data") or {}
                status = data.get("status")
                if status == "completed":
                    url = data.get("video_url") or data.get("url")
                    if url:
                        return url
                    raise Exception("Video completed but no URL returned")
                if status == "failed":
                    raise Exception(
                        f"HeyGen video generation failed: "
                        f"{data.get('failure_message') or data.get('error') or data}"
                    )
            else:
                # legacy fallback
                legacy = await client.get(
                    f"{self.v1}/video_status.get",
                    params={"video_id": video_id},
                    headers=self._headers(),
                )
                if legacy.status_code != 200:
                    raise Exception(
                        f"HeyGen status check error: v3={status_response.text}; "
                        f"legacy={legacy.text}"
                    )
                data = legacy.json().get("data") or {}
                status = data.get("status")
                if status == "completed":
                    url = data.get("video_url")
                    if url:
                        return url
                    raise Exception("Video completed but no URL returned")
                if status == "failed":
                    raise Exception(
                        f"HeyGen video generation failed: {data.get('error', data)}"
                    )

            await asyncio.sleep(poll_interval)

        raise Exception(
            f"Video generation timed out after {max_attempts * poll_interval} seconds"
        )

    async def get_avatar_info(self, avatar_id: str) -> dict:
        avatars = await self.list_avatars()
        for avatar in avatars:
            if avatar.get("avatar_id") == avatar_id:
                return avatar
        raise Exception(f"Avatar {avatar_id} not found")
