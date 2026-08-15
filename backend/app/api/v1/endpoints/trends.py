"""Client trend radar endpoints."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_client, get_db
from app.core.config import settings
from app.models.client import Client
from app.models.trend import TrendInsight, TrendItem
from app.schemas.trend import (
    TrendAnalyzeRequest,
    TrendInsightOut,
    TrendItemOut,
    TrendsListResponse,
)
from app.services.jobs import enqueue_job
from app.services.trends import TrendsService
from app.services.trends.study_pipeline import TrendStudyPipeline

router = APIRouter()


def _item_out(row: TrendItem) -> TrendItemOut:
    return TrendItemOut.model_validate(row)


@router.get("/trends", response_model=TrendsListResponse)
async def list_trends(
    platform: str = Query("youtube", description="youtube|tiktok|instagram|vk"),
    region: str = Query(""),
    niche: str = Query(""),
    limit: int = Query(15, ge=3, le=30),
    refresh: bool = Query(False),
    client: Client = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """List cached trends; optionally refresh from providers."""
    svc = TrendsService(db)
    region_norm = (region or settings.TRENDS_DEFAULT_REGION or "RU").upper()
    items, meta = await svc.refresh(
        platform=platform,
        region=region_norm,
        niche=niche,
        limit=limit,
        force=refresh,
    )
    meta["youtube_configured"] = bool(settings.YOUTUBE_API_KEY)
    meta["tiktok_enabled"] = bool(settings.TIKTOK_TRENDS_ENABLED)
    meta["analyze_credits"] = int(settings.TRENDS_ANALYZE_COST_CREDITS or 0)
    return TrendsListResponse(items=[_item_out(i) for i in items], meta=meta)


@router.post("/trends/analyze", response_model=TrendInsightOut, status_code=status.HTTP_201_CREATED)
async def analyze_trend(
    body: TrendAnalyzeRequest,
    background_tasks: BackgroundTasks,
    client: Client = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """Enqueue study of a public video URL (research patterns, not 1:1 copy)."""
    url = (body.url or "").strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        raise HTTPException(status_code=400, detail="Нужна http(s) ссылка на ролик")

    cost = max(0, int(settings.TRENDS_ANALYZE_COST_CREDITS or 0))
    if cost and client.credits_remaining < cost:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Нужно {cost} кредит(ов) для разбора тренда",
        )

    trend_item_id = body.trend_item_id
    if trend_item_id:
        result = await db.execute(select(TrendItem).where(TrendItem.id == trend_item_id))
        if not result.scalar_one_or_none():
            trend_item_id = None

    insight = TrendInsight(
        client_id=client.id,
        trend_item_id=trend_item_id,
        source_url=url,
        status="pending",
        progress_percent=0,
    )
    db.add(insight)
    await db.commit()
    await db.refresh(insight)

    async def _run(insight_id: int, client_id: int):
        from app.db.session import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            await TrendStudyPipeline(session).process(insight_id, client_id)

    enqueue_job(
        background_tasks,
        "trend_study",
        insight.id,
        client.id,
        _run,
    )
    return TrendInsightOut.model_validate(insight)


@router.get("/trends/insights", response_model=list[TrendInsightOut])
async def list_insights(
    limit: int = Query(20, ge=1, le=50),
    client: Client = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(TrendInsight)
        .where(TrendInsight.client_id == client.id)
        .order_by(desc(TrendInsight.id))
        .limit(limit)
    )
    return [TrendInsightOut.model_validate(r) for r in result.scalars().all()]


@router.get("/trends/insights/{insight_id}", response_model=TrendInsightOut)
async def get_insight(
    insight_id: int,
    client: Client = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(TrendInsight).where(
            TrendInsight.id == insight_id,
            TrendInsight.client_id == client.id,
        )
    )
    insight = result.scalar_one_or_none()
    if not insight:
        raise HTTPException(status_code=404, detail="Insight not found")
    return TrendInsightOut.model_validate(insight)


@router.post("/trends/insights/{insight_id}/apply-viral", response_model=dict)
async def apply_insight_to_viral(
    insight_id: int,
    client: Client = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """
    Return prefill payload for viral-edit form (style + hook + source_url).
    Frontend fills the viral page; does not start a job by itself.
    """
    result = await db.execute(
        select(TrendInsight).where(
            TrendInsight.id == insight_id,
            TrendInsight.client_id == client.id,
        )
    )
    insight = result.scalar_one_or_none()
    if not insight:
        raise HTTPException(status_code=404, detail="Insight not found")
    if insight.status != "completed":
        raise HTTPException(status_code=400, detail="Разбор ещё не готов")

    return {
        "source_url": insight.source_url,
        "style": insight.style_guess or "dynamic",
        "hook_hint": insight.hook_text or "",
        "tips": insight.tips or [],
        "insight_id": insight.id,
        "message": "Откройте «Вирусный монтаж» — поля будут подставлены",
    }
