"""Download source video from a public URL (YouTube, Reels, TikTok, …).

Primary: yt-dlp. Optional: self-hosted Cobalt API when COBALT_API_URL is set.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
import uuid
from typing import Optional, Tuple
from urllib.parse import urlparse

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

ALLOWED_VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}


class MediaIngestError(Exception):
    """User-facing ingest failure."""


def validate_public_http_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw or len(raw) > 2048:
        raise MediaIngestError("Укажите корректную ссылку на видео")

    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise MediaIngestError("Ссылка должна начинаться с http:// или https://")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise MediaIngestError("В ссылке нет хоста")
    if host in {"localhost", "metadata.google.internal"} or host.endswith(".local"):
        raise MediaIngestError("Эта ссылка недоступна для импорта")

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise MediaIngestError("Не удалось разрешить адрес ссылки") from exc

    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except (ValueError, IndexError):
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise MediaIngestError("Импорт с внутренних адресов запрещён")

    return raw


def _pick_downloaded_file(directory: str, stem: str) -> Optional[str]:
    for name in os.listdir(directory):
        if not name.startswith(stem):
            continue
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext in ALLOWED_VIDEO_EXT or ext in {".mp4", ".mkv", ".webm", ".mov"}:
            return path
    return None


def _ytdlp_cookie_opts() -> dict:
    """Optional browser cookies / cookie file to pass YouTube bot checks."""
    opts: dict = {}
    cookie_file = (settings.YTDLP_COOKIES_FILE or "").strip()
    if cookie_file:
        candidates = [cookie_file]
        if not os.path.isabs(cookie_file):
            here = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            candidates.append(os.path.join(here, cookie_file.replace("./", "")))
            candidates.append(os.path.abspath(cookie_file))
        resolved = next((p for p in candidates if os.path.isfile(p)), None)
        if resolved:
            opts["cookiefile"] = resolved
        else:
            logger.warning("YTDLP_COOKIES_FILE not found: %s", cookie_file)

    browser = (settings.YTDLP_COOKIES_FROM_BROWSER or "").strip()
    if browser and "cookiefile" not in opts:
        # chrome / edge / firefox  or  chrome:ProfileName
        if ":" in browser:
            name, profile = browser.split(":", 1)
            opts["cookiesfrombrowser"] = (name.strip(), profile.strip(), None, None)
        else:
            opts["cookiesfrombrowser"] = (browser,)
    return opts


def _is_youtube_bot_block(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return (
        "sign in to confirm" in msg
        or "not a bot" in msg
        or "confirm you're not a bot" in msg
        or "confirm you are not a bot" in msg
    )


def _ytdlp_download(url: str, dest_dir: str, max_bytes: int) -> Tuple[str, str]:
    try:
        import yt_dlp
        from yt_dlp.utils import DownloadError
    except ImportError as exc:
        raise MediaIngestError("yt-dlp не установлен на сервере") from exc

    stem = uuid.uuid4().hex
    outtmpl = os.path.join(dest_dir, f"{stem}.%(ext)s")

    def _base_opts(player_clients: list[str], fmt: str) -> dict:
        return {
            "format": fmt,
            "merge_output_format": "mp4",
            "outtmpl": outtmpl,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "restrictfilenames": True,
            "socket_timeout": 45,
            "retries": 2,
            "max_filesize": max_bytes,
            # Node solves YouTube n-challenge (otherwise only storyboards)
            "js_runtimes": {"node": {}},
            "remote_components": {"ejs:npm"},
            "extractor_args": {
                "youtube": {"player_client": player_clients},
            },
        }

    cookie_opts = _ytdlp_cookie_opts()
    formats = [
        "bestvideo*+bestaudio/best",
        "bv*[height<=1080]+ba/b",
        "best",
    ]
    # Web cookies + android client → often only storyboards ("format not available").
    # Prefer web clients when cookiefile is present.
    if cookie_opts.get("cookiefile") or cookie_opts.get("cookiesfrombrowser"):
        client_sets = [
            ["web"],
            ["web_safari", "web"],
            ["mweb", "web"],
            ["tv_embedded", "web"],
        ]
    else:
        client_sets = [
            ["android", "ios", "web"],
            ["tv_embedded", "android"],
            ["web"],
        ]

    attempts: list[dict] = []
    for clients in client_sets:
        for fmt in formats:
            o = _base_opts(clients, fmt)
            # Prefer cookie-authenticated attempts first
            if cookie_opts:
                with_ck = dict(o)
                with_ck.update(cookie_opts)
                attempts.append(with_ck)
            attempts.append(o)

    info = None
    last_exc: Optional[BaseException] = None
    for opts in attempts:
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
            last_exc = None
            break
        except Exception as exc:
            last_exc = exc
            msg = str(exc).lower()
            retryable = (
                "cookie database" in msg
                or "could not copy" in msg
                or "format is not available" in msg
                or "requested format" in msg
                or _is_youtube_bot_block(exc)
                or isinstance(exc, DownloadError)
            )
            if retryable:
                logger.warning("yt-dlp attempt failed, trying next: %s", str(exc)[:180])
                continue
            raise

    if last_exc is not None or info is None:
        exc = last_exc or RuntimeError("yt-dlp failed")
        if _is_youtube_bot_block(exc) or "cookie database" in str(exc).lower():
            raise MediaIngestError(
                "YouTube блокирует скачивание (бот-проверка / cookies). "
                "Обнови cookies: Edge → Get cookies.txt LOCALLY → "
                ".\\scripts\\import_youtube_cookies.ps1 "
                "Или загрузи файл видео вручную."
            ) from exc
        raise MediaIngestError(f"Не удалось скачать видео ({str(exc)[:200]})") from exc

    path = _pick_downloaded_file(dest_dir, stem)
    if not path or not os.path.isfile(path):
        raise MediaIngestError("Не удалось сохранить скачанное видео")

    size = os.path.getsize(path)
    if size <= 0:
        raise MediaIngestError("Скачанный файл пустой")
    if size > max_bytes:
        try:
            os.remove(path)
        except OSError:
            pass
        raise MediaIngestError(
            f"Видео больше лимита ({max_bytes // (1024 * 1024)} МБ)"
        )

    title = ""
    if isinstance(info, dict):
        title = str(info.get("title") or info.get("id") or "").strip()
    display = title or os.path.basename(path)
    return path, display[:200]


async def _cobalt_download(url: str, dest_dir: str, max_bytes: int) -> Tuple[str, str]:
    base = (settings.COBALT_API_URL or "").rstrip("/")
    if not base:
        raise MediaIngestError("Cobalt не настроен")

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if settings.COBALT_API_KEY:
        headers["Authorization"] = f"Api-Key {settings.COBALT_API_KEY}"

    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        resp = await client.post(f"{base}/", json={"url": url}, headers=headers)
        try:
            data = resp.json()
        except Exception as exc:
            raise MediaIngestError(f"Cobalt вернул не JSON ({resp.status_code})") from exc

        status = str(data.get("status") or "")
        if status == "error":
            raise MediaIngestError(str(data.get("error") or data.get("text") or "Cobalt error")[:300])
        if status == "picker":
            # Take first video-looking item
            items = data.get("picker") or []
            media_url = None
            for item in items:
                if isinstance(item, dict) and item.get("url"):
                    media_url = item["url"]
                    break
            if not media_url:
                raise MediaIngestError("Cobalt: нет файла в picker")
        elif status in {"redirect", "tunnel", "stream"}:
            media_url = data.get("url")
            if not media_url:
                raise MediaIngestError("Cobalt: пустой url")
        else:
            media_url = data.get("url")
            if not media_url:
                raise MediaIngestError(f"Cobalt: неизвестный ответ ({status or 'empty'})")

        # media_url is a CDN/tunnel — still validate scheme; host may be CDN
        parsed = urlparse(str(media_url))
        if parsed.scheme not in {"http", "https"}:
            raise MediaIngestError("Cobalt вернул недопустимую ссылку на файл")

        stem = uuid.uuid4().hex
        tmp_path = os.path.join(dest_dir, f"{stem}.bin")
        size = 0
        async with client.stream("GET", str(media_url)) as stream:
            stream.raise_for_status()
            with open(tmp_path, "wb") as out:
                async for chunk in stream.aiter_bytes(1024 * 256):
                    size += len(chunk)
                    if size > max_bytes:
                        out.close()
                        try:
                            os.remove(tmp_path)
                        except OSError:
                            pass
                        raise MediaIngestError(
                            f"Видео больше лимита ({max_bytes // (1024 * 1024)} МБ)"
                        )
                    out.write(chunk)

    # Guess extension from content-type or default mp4
    ext = ".mp4"
    ctype = ""
    # reopen not needed — use .mp4 rename
    final_path = os.path.join(dest_dir, f"{stem}{ext}")
    os.replace(tmp_path, final_path)
    return final_path, f"cobalt_{stem}{ext}"


async def download_video_from_url(
    url: str,
    dest_dir: str,
    max_bytes: int,
) -> Tuple[str, str]:
    """
    Download video to dest_dir.
    Returns (absolute_path, display_name).
    """
    if not settings.LINK_INGEST_ENABLED:
        raise MediaIngestError("Импорт по ссылке отключён")

    safe_url = validate_public_http_url(url)
    os.makedirs(dest_dir, exist_ok=True)

    errors: list[str] = []

    if settings.COBALT_API_URL:
        try:
            path, name = await _cobalt_download(safe_url, dest_dir, max_bytes)
            logger.info("Ingest via Cobalt → %s", path)
            return path, name
        except Exception as exc:
            logger.warning("Cobalt ingest failed: %s", exc)
            errors.append(f"Cobalt: {exc}")

    try:
        path, name = await asyncio.to_thread(_ytdlp_download, safe_url, dest_dir, max_bytes)
        logger.info("Ingest via yt-dlp → %s", path)
        return path, name
    except MediaIngestError:
        raise
    except Exception as exc:
        logger.exception("yt-dlp ingest failed")
        if _is_youtube_bot_block(exc):
            raise MediaIngestError(
                "YouTube просит подтвердить, что вы не бот. "
                "В backend/.env укажите YTDLP_COOKIES_FROM_BROWSER=edge "
                "(или chrome) и перезапустите API."
            ) from exc
        errors.append(f"yt-dlp: {exc}")
        detail = "; ".join(errors) if errors else str(exc)
        raise MediaIngestError(
            f"Не удалось скачать видео. Проверьте ссылку и доступность. ({detail[:240]})"
        ) from exc
