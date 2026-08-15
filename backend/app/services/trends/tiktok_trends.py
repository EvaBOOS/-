"""TikTok trend discovery via Creative Center public endpoints (unofficial, fragile)."""
from __future__ import annotations

import logging
from typing import Any, Dict, List

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Creative Center hashtag list (may change; fail soft)
CC_HASHTAG_URL = (
    "https://ads.tiktok.com/creative_radar_api/v1/top_ads/v2/list"
)
# Alternate lighter endpoint used by some open-source trend tools
CC_POPULAR_HASHTAGS = (
    "https://ads.tiktok.com/business/creativecenter/inspiration/popular/hashtag/pad/v1/"
)


class TikTokTrendsError(Exception):
    pass


async def fetch_tiktok_trends(
    *,
    region: str = "RU",
    niche: str = "",
    limit: int = 15,
) -> List[Dict[str, Any]]:
    """
    Best-effort hashtag trends. Returns empty-safe structured items.
    Does not download videos — metadata / search hints only.
    """
    if not settings.TIKTOK_TRENDS_ENABLED:
        raise TikTokTrendsError("TikTok trends отключены (TIKTOK_TRENDS_ENABLED=false)")

    region = (region or "RU").upper()[:8]
    limit = max(3, min(int(limit or 15), 25))
    niche_q = (niche or "").strip().lower()

    items: List[Dict[str, Any]] = []
    try:
        items = await _fetch_popular_hashtags(region, limit)
    except Exception as exc:
        logger.warning("TikTok Creative Center failed: %s", exc)
        raise TikTokTrendsError(
            f"TikTok тренды временно недоступны ({exc})"
        ) from exc

    if niche_q:
        filtered = [
            x for x in items
            if niche_q in (x.get("title") or "").lower()
            or niche_q in str((x.get("metrics") or {}).get("hashtag") or "").lower()
        ]
        items = filtered or items

    return items[:limit]


async def _fetch_popular_hashtags(region: str, limit: int) -> List[Dict[str, Any]]:
    """
    Hit Creative Center popular hashtags page API.
    Country codes: RU, US, GB, …
    """
    period = 7
    page = 1
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://ads.tiktok.com/business/creativecenter/inspiration/popular/hashtag",
    }
    params = {
        "period": period,
        "page": page,
        "limit": min(limit, 20),
        "country_code": region,
        "sort_by": "popular",
    }

    async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
        r = await client.get(CC_POPULAR_HASHTAGS, params=params, headers=headers)
        if r.status_code != 200:
            raise TikTokTrendsError(f"HTTP {r.status_code}")
        try:
            data = r.json()
        except Exception as exc:
            raise TikTokTrendsError(
                "Creative Center вернул не JSON (часто блок/гео/капча). "
                "Пока используйте YouTube Shorts или вставьте ссылку вручную."
            ) from exc

    # Response shapes vary; try common paths
    rows = (
        (data.get("data") or {}).get("list")
        or (data.get("data") or {}).get("hashtags")
        or data.get("list")
        or []
    )
    if not isinstance(rows, list) or not rows:
        raise TikTokTrendsError("пустой список хештегов")

    out: List[Dict[str, Any]] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        tag = (
            row.get("hashtag_name")
            or row.get("hashtagName")
            or row.get("name")
            or row.get("title")
            or ""
        )
        tag = str(tag).lstrip("#").strip()
        if not tag:
            continue
        rank = row.get("rank") or row.get("position") or (i + 1)
        video_views = (
            row.get("video_views")
            or row.get("videoViews")
            or row.get("views")
            or row.get("publish_cnt")
        )
        # Search URL (user can open / we can later resolve a sample)
        url = f"https://www.tiktok.com/tag/{tag}"
        out.append({
            "platform": "tiktok",
            "external_id": f"tag:{tag.lower()}",
            "title": f"#{tag}",
            "url": url,
            "niche": None,
            "region": region,
            "metrics": {
                "hashtag": tag,
                "rank": rank,
                "video_views": video_views,
                "source": "tiktok_creative_center",
                "note": "Хештег-тренд; конкретный ролик выбирайте вручную или через поиск",
            },
        })
    if not out:
        raise TikTokTrendsError("не удалось разобрать ответ")
    return out
