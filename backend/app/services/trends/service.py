"""Trend radar orchestration: fetch → cache in DB → list."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.trend import TrendItem
from app.services.trends.youtube_trends import YouTubeTrendsError, fetch_youtube_trends
from app.services.trends.tiktok_trends import TikTokTrendsError, fetch_tiktok_trends

logger = logging.getLogger(__name__)


def _aware_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class TrendsService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_cached(
        self,
        *,
        platform: str,
        region: str,
        niche: str = "",
        limit: int = 20,
    ) -> List[TrendItem]:
        platform = (platform or "").lower().strip()
        region = (region or settings.TRENDS_DEFAULT_REGION).upper()
        q = (
            select(TrendItem)
            .where(TrendItem.platform == platform)
            .where(TrendItem.region == region)
            .order_by(desc(TrendItem.fetched_at), desc(TrendItem.id))
            .limit(limit)
        )
        if niche.strip():
            q = q.where(TrendItem.niche == niche.strip().lower())
        result = await self.db.execute(q)
        return list(result.scalars().all())

    async def cache_fresh(
        self,
        *,
        platform: str,
        region: str,
        niche: str = "",
    ) -> bool:
        """True if cache still fresh for this slice."""
        items = await self.list_cached(platform=platform, region=region, niche=niche, limit=1)
        if not items:
            return False
        fetched = _as_aware(items[0].fetched_at)
        if not fetched:
            return False
        age = _aware_now() - fetched
        return age < timedelta(hours=max(1, int(settings.TRENDS_CACHE_HOURS or 6)))

    async def refresh(
        self,
        *,
        platform: str,
        region: str = "",
        niche: str = "",
        limit: int = 15,
        force: bool = False,
    ) -> Tuple[List[TrendItem], Dict[str, Any]]:
        platform = (platform or "youtube").lower().strip()
        region = (region or settings.TRENDS_DEFAULT_REGION or "RU").upper()
        niche_norm = (niche or "").strip().lower()
        meta: Dict[str, Any] = {"platform": platform, "region": region, "niche": niche_norm or None}

        if platform in {"instagram", "vk"}:
            meta["status"] = "coming_soon"
            meta["message"] = "Площадка в планах MVP-2"
            return [], meta

        if not force and await self.cache_fresh(platform=platform, region=region, niche=niche_norm):
            cached = await self.list_cached(
                platform=platform, region=region, niche=niche_norm, limit=limit
            )
            meta["status"] = "cache_hit"
            meta["count"] = len(cached)
            return cached, meta

        try:
            if platform == "youtube":
                raw = await fetch_youtube_trends(region=region, niche=niche_norm, limit=limit)
            elif platform == "tiktok":
                raw = await fetch_tiktok_trends(region=region, niche=niche_norm, limit=limit)
            else:
                meta["status"] = "unsupported"
                meta["message"] = f"Неизвестная платформа: {platform}"
                return [], meta
        except (YouTubeTrendsError, TikTokTrendsError) as exc:
            cached = await self.list_cached(
                platform=platform, region=region, niche=niche_norm, limit=limit
            )
            meta["status"] = "error"
            meta["message"] = str(exc)
            meta["count"] = len(cached)
            return cached, meta
        except Exception as exc:
            logger.exception("trends refresh failed")
            meta["status"] = "error"
            meta["message"] = str(exc)[:300]
            return [], meta

        saved = await self._upsert_many(raw, niche_override=niche_norm or None)
        meta["status"] = "refreshed"
        meta["count"] = len(saved)
        return saved, meta

    async def _upsert_many(
        self,
        raw_items: List[Dict[str, Any]],
        niche_override: Optional[str] = None,
    ) -> List[TrendItem]:
        now = _aware_now()
        saved: List[TrendItem] = []
        for raw in raw_items:
            platform = str(raw.get("platform") or "")
            external_id = str(raw.get("external_id") or "")
            if not platform or not external_id:
                continue
            result = await self.db.execute(
                select(TrendItem).where(
                    and_(
                        TrendItem.platform == platform,
                        TrendItem.external_id == external_id,
                    )
                )
            )
            row = result.scalar_one_or_none()
            niche_val = niche_override or raw.get("niche")
            if row:
                row.title = raw.get("title") or row.title
                row.url = raw.get("url") or row.url
                row.niche = niche_val or row.niche
                row.region = raw.get("region") or row.region
                row.metrics = raw.get("metrics") or row.metrics
                row.fetched_at = now
            else:
                row = TrendItem(
                    platform=platform,
                    external_id=external_id,
                    title=raw.get("title"),
                    url=raw.get("url"),
                    niche=niche_val,
                    region=raw.get("region"),
                    metrics=raw.get("metrics") or {},
                    fetched_at=now,
                )
                self.db.add(row)
            saved.append(row)
        await self.db.commit()
        for row in saved:
            await self.db.refresh(row)
        return saved
