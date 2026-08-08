"""Speech-to-text via AITUNNEL / OpenAI-compatible /audio/transcriptions."""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings


class WhisperService:
    def __init__(self) -> None:
        aitunnel_key = (settings.AITUNNEL_API_KEY or "").strip()
        openai_key = (settings.OPENAI_API_KEY or "").strip()
        if aitunnel_key:
            self.api_key = aitunnel_key
            self.base_url = settings.AITUNNEL_BASE_URL.rstrip("/")
            self.provider = "aitunnel"
        else:
            self.api_key = openai_key or None
            self.base_url = "https://api.openai.com/v1"
            self.provider = "openai"
        self.model = settings.WHISPER_MODEL

    def _require_key(self) -> None:
        if not self.api_key:
            raise ValueError(
                "STT key missing. Set AITUNNEL_API_KEY or OPENAI_API_KEY for Whisper."
            )

    async def transcribe(
        self,
        media_path: str,
        language: Optional[str] = "ru",
    ) -> Dict[str, Any]:
        """
        Returns:
          {
            text: str,
            words: [{word, start, end}, ...],
            segments: [{text, start, end}, ...],
            duration: float | None,
          }
        """
        self._require_key()
        if not os.path.isfile(media_path):
            raise FileNotFoundError(media_path)

        # Prefer verbose_json + word timestamps; fall back to plain json.
        data = await self._post_transcription(
            media_path,
            language=language,
            response_format="verbose_json",
            want_words=True,
        )
        if data is None:
            data = await self._post_transcription(
                media_path,
                language=language,
                response_format="json",
                want_words=False,
            )

        text = (data.get("text") or "").strip()
        words = self._extract_words(data)
        segments = self._extract_segments(data)
        duration = data.get("duration")

        if not words and text and duration:
            words = self._approximate_words(text, float(duration))
        elif not words and text and segments:
            words = self._words_from_segments(segments)
        elif not words and text:
            # last resort: 0.35s per word
            words = self._approximate_words(text, max(0.35 * len(text.split()), 1.0))

        if words and text:
            words = self._reattach_punctuation(words, text)

        return {
            "text": text,
            "words": words,
            "segments": segments,
            "duration": duration,
            "provider": self.provider,
            "model": self.model,
        }

    async def transcribe_long(
        self,
        media_path: str,
        language: Optional[str] = "ru",
        chunk_seconds: float = 480.0,
        max_bytes: int = 20 * 1024 * 1024,
    ) -> Dict[str, Any]:
        """
        Transcribe long audio/video by chunking when file is too large for STT APIs.
        """
        size = os.path.getsize(media_path)
        if size <= max_bytes:
            return await self.transcribe(media_path, language=language)

        from app.services.video.ffmpeg_service import FFmpegService

        ff = FFmpegService()
        duration = ff.get_video_duration(media_path)
        if duration <= 0:
            return await self.transcribe(media_path, language=language)

        work = os.path.join(os.path.dirname(media_path) or ".", "_whisper_chunks")
        os.makedirs(work, exist_ok=True)

        all_text: List[str] = []
        all_words: List[Dict[str, Any]] = []
        all_segments: List[Dict[str, Any]] = []
        offset = 0.0
        idx = 0
        while offset < duration - 0.5:
            chunk_path = os.path.join(work, f"chunk_{idx:03d}.mp3")
            dur = min(chunk_seconds, duration - offset)
            ff.extract_audio_segment(media_path, chunk_path, start=offset, duration=dur)
            part = await self.transcribe(chunk_path, language=language)
            if part.get("text"):
                all_text.append(part["text"])
            for w in part.get("words") or []:
                all_words.append({
                    "word": w["word"],
                    "start": float(w["start"]) + offset,
                    "end": float(w["end"]) + offset,
                })
            for s in part.get("segments") or []:
                all_segments.append({
                    "text": s["text"],
                    "start": float(s["start"]) + offset,
                    "end": float(s["end"]) + offset,
                })
            offset += chunk_seconds
            idx += 1

        return {
            "text": " ".join(all_text).strip(),
            "words": all_words,
            "segments": all_segments,
            "duration": duration,
            "provider": self.provider,
            "model": self.model,
            "chunks": idx,
        }

    async def _post_transcription(
        self,
        media_path: str,
        language: Optional[str],
        response_format: str,
        want_words: bool,
    ) -> Optional[Dict[str, Any]]:
        filename = os.path.basename(media_path)
        mime = self._guess_mime(filename)
        with open(media_path, "rb") as f:
            file_bytes = f.read()

        files = {"file": (filename, file_bytes, mime)}
        data: Dict[str, Any] = {
            "model": self.model,
            "response_format": response_format,
        }
        if language:
            data["language"] = language
        if want_words:
            # OpenAI-style; AITUNNEL may ignore
            data["timestamp_granularities[]"] = "word"

        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                f"{self.base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                data=data,
                files=files,
            )

        if resp.status_code != 200:
            if want_words:
                return None
            try:
                err = resp.json()
            except Exception:
                err = resp.text
            raise Exception(
                f"{self.provider} transcription error ({resp.status_code}): {err}"
            )

        payload = resp.json()
        if isinstance(payload, str):
            return {"text": payload}
        return payload

    @staticmethod
    def _guess_mime(filename: str) -> str:
        ext = os.path.splitext(filename)[1].lower()
        return {
            ".mp3": "audio/mpeg",
            ".wav": "audio/wav",
            ".m4a": "audio/mp4",
            ".mp4": "video/mp4",
            ".webm": "video/webm",
            ".ogg": "audio/ogg",
            ".flac": "audio/flac",
        }.get(ext, "application/octet-stream")

    @staticmethod
    def _extract_words(data: Dict[str, Any]) -> List[Dict[str, Any]]:
        raw = data.get("words") or []
        out = []
        for w in raw:
            word = (w.get("word") or w.get("text") or "").strip()
            if not word:
                continue
            out.append({
                "word": word,
                "start": float(w.get("start") or 0),
                "end": float(w.get("end") or w.get("start") or 0),
            })
        return out

    @staticmethod
    def _extract_segments(data: Dict[str, Any]) -> List[Dict[str, Any]]:
        raw = data.get("segments") or []
        out = []
        for s in raw:
            text = (s.get("text") or "").strip()
            if not text:
                continue
            out.append({
                "text": text,
                "start": float(s.get("start") or 0),
                "end": float(s.get("end") or s.get("start") or 0),
            })
        return out

    @staticmethod
    def _reattach_punctuation(words: List[Dict[str, Any]], text: str) -> List[Dict[str, Any]]:
        """The word-timestamps API returns bare tokens ("паузы", "секунд")
        with no punctuation, even though `text` has it ("паузы,", "секунд?")
        — captions built straight from `words` end up with no commas,
        periods or question marks at all. Re-attach it positionally from
        `text`, which tokenizes 1:1 with `words` in the overwhelming
        majority of real transcripts; on any mismatch (STT split the two
        differently), leave `words` untouched rather than risk misaligning
        them."""
        tokens = re.findall(r"\S+", text)
        if len(tokens) != len(words):
            return words
        out = []
        for w, tok in zip(words, tokens):
            out.append({**w, "word": tok})
        return out

    @staticmethod
    def _approximate_words(text: str, duration: float) -> List[Dict[str, Any]]:
        tokens = [t for t in re.findall(r"\S+", text) if t]
        if not tokens:
            return []
        slot = duration / len(tokens)
        out = []
        for i, tok in enumerate(tokens):
            start = i * slot
            out.append({"word": tok, "start": start, "end": start + slot * 0.95})
        return out

    @staticmethod
    def _words_from_segments(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for seg in segments:
            tokens = [t for t in re.findall(r"\S+", seg["text"]) if t]
            if not tokens:
                continue
            span = max(seg["end"] - seg["start"], 0.1)
            slot = span / len(tokens)
            for i, tok in enumerate(tokens):
                start = seg["start"] + i * slot
                out.append({"word": tok, "start": start, "end": start + slot * 0.95})
        return out
