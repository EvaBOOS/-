"""YouTube Shorts discovery via Data API v3 (never long-form chart)."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
# Shorts are typically ≤60s; API videoDuration=short means <4min — we filter harder.
MAX_SHORT_SECONDS = 60


class YouTubeTrendsError(Exception):
    pass


async def fetch_youtube_trends(
    *,
    region: str = "RU",
    niche: str = "",
    limit: int = 15,
) -> List[Dict[str, Any]]:
    """
    Return list of trend dicts (Shorts only):
      platform, external_id, title, url, niche, region, metrics
    """
    key = (settings.YOUTUBE_API_KEY or "").strip()
    if not key:
        raise YouTubeTrendsError(
            "YOUTUBE_API_KEY не задан. Добавьте бесплатный ключ Data API v3 в .env"
        )

    region = (region or settings.TRENDS_DEFAULT_REGION or "RU").upper()[:8]
    limit = max(3, min(int(limit or 15), 25))
    niche_q = (niche or "").strip()

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Always search #shorts — never mostPopular (that returns trailers / long-form).
        items = await _search_shorts(client, key, region, niche_q, max(limit * 3, 25))
        shorts = [x for x in items if _looks_short(x)]
        if not shorts:
            # Soften once: allow up to 90s if region has few true ≤60s hits
            shorts = [x for x in items if _duration_seconds(x) <= 90]
        if region == "RU" and shorts:
            cyr = [
                x for x in shorts
                if any("\u0400" <= ch <= "\u04FF" for ch in str((x.get("snippet") or {}).get("title") or ""))
            ]
            if cyr:
                shorts = cyr + [x for x in shorts if x not in cyr]
        if not shorts:
            raise YouTubeTrendsError(
                "Не нашли YouTube Shorts по этому запросу. Смените нишу или регион."
            )

    out: List[Dict[str, Any]] = []
    for it in shorts[:limit]:
        vid = it.get("id") or ""
        if isinstance(vid, dict):
            vid = vid.get("videoId") or ""
        if not vid:
            continue
        snippet = it.get("snippet") or {}
        stats = it.get("statistics") or {}
        content = it.get("contentDetails") or {}
        title = str(snippet.get("title") or "").strip() or f"YouTube {vid}"
        out.append({
            "platform": "youtube",
            "external_id": str(vid),
            "title": title[:500],
            "url": f"https://www.youtube.com/shorts/{vid}",
            "niche": niche_q or None,
            "region": region,
            "metrics": {
                "channel": snippet.get("channelTitle"),
                "views": _int(stats.get("viewCount")),
                "likes": _int(stats.get("likeCount")),
                "duration": content.get("duration"),
                "duration_sec": _duration_seconds(it),
                "thumb": ((snippet.get("thumbnails") or {}).get("medium") or {}).get("url"),
                "published_at": snippet.get("publishedAt"),
                "source": "youtube_data_api_shorts",
            },
        })
    return out


async def _search_shorts(
    client: httpx.AsyncClient,
    key: str,
    region: str,
    niche: str,
    limit: int,
) -> List[Dict[str, Any]]:
    q = f"{niche} #shorts".strip() if niche else "shorts #shorts"
    params: Dict[str, Any] = {
        "part": "snippet",
        "q": q,
        "type": "video",
        "videoDuration": "short",
        "order": "viewCount" if niche else "date",
        "regionCode": region,
        "maxResults": min(max(limit, 10), 50),
        "key": key,
    }
    if region == "RU":
        params["relevanceLanguage"] = "ru"

    r = await client.get(f"{YOUTUBE_API}/search", params=params)
    if r.status_code != 200:
        raise YouTubeTrendsError(f"YouTube search error {r.status_code}: {r.text[:240]}")
    search_items = (r.json() or {}).get("items") or []
    ids: List[str] = []
    for it in search_items:
        vid = ((it.get("id") or {}).get("videoId")) if isinstance(it.get("id"), dict) else None
        if vid:
            ids.append(vid)
    if not ids:
        return []
    r2 = await client.get(
        f"{YOUTUBE_API}/videos",
        params={
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(ids),
            "key": key,
        },
    )
    if r2.status_code != 200:
        raise YouTubeTrendsError(f"YouTube videos error {r2.status_code}: {r2.text[:240]}")
    return list((r2.json() or {}).get("items") or [])


def _duration_seconds(item: Dict[str, Any]) -> int:
    dur = str((item.get("contentDetails") or {}).get("duration") or "")
    if not dur.startswith("PT"):
        return 9999
    body = dur[2:]
    hours = 0
    minutes = 0
    seconds = 0
    num = ""
    for ch in body:
        if ch.isdigit():
            num += ch
            continue
        if ch == "H" and num:
            hours = int(num)
            num = ""
        elif ch == "M" and num:
            minutes = int(num)
            num = ""
        elif ch == "S" and num:
            seconds = int(num)
            num = ""
    return hours * 3600 + minutes * 60 + seconds


def _looks_short(item: Dict[str, Any]) -> bool:
    return _duration_seconds(item) <= MAX_SHORT_SECONDS


def _int(v: Optional[str]) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None
