"""Admin hold queue, review decisions, rights-holder complaints."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_admin, get_db
from app.models.generation import GenerationStatus, VideoGeneration
from app.models.moderation import ModerationEvent, RightsComplaint
from app.models.user import User
from app.services.jobs import requeue_generation
from app.services.moderation.runtime import KIND_BY_MODE

router = APIRouter()


class ReviewBody(BaseModel):
    note: str = Field("", max_length=2000)


class ComplaintNote(BaseModel):
    note: str = Field("", max_length=2000)
    status: str = Field("resolved", max_length=20)


def _snippet(generation: VideoGeneration) -> str:
    text = generation.generated_script or generation.original_text or ""
    meta = generation.api_responses or {}
    if "CSAE" in (meta.get("moderation") or {}).get("categories") or []:
        return ""
    return text[:1500]


@router.get("/moderation/queue")
async def moderation_queue(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    result = await db.execute(
        select(VideoGeneration)
        .options(selectinload(VideoGeneration.client))
        .where(VideoGeneration.status == GenerationStatus.MODERATION_HOLD)
        .order_by(VideoGeneration.created_at.asc())
        .limit(100)
    )
    items = []
    for g in result.scalars().all():
        meta = (g.api_responses or {}).get("moderation") or {}
        client = g.client
        items.append({
            "id": g.id,
            "client_id": g.client_id,
            "company": (client.company_name if client else None) or f"#{g.client_id}",
            "mode": g.mode,
            "status": g.status.value if hasattr(g.status, "value") else str(g.status),
            "reason": meta.get("reason") or g.error_message,
            "categories": meta.get("categories") or [],
            "stage": meta.get("stage"),
            "appealed": bool(meta.get("appealed")),
            "snippet": _snippet(g),
            "source_url": (g.api_responses or {}).get("source_url"),
            "created_at": g.created_at.isoformat() if g.created_at else None,
        })
    return {"items": items}


@router.get("/moderation/stats")
async def moderation_stats(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    total_q = await db.execute(select(func.count(VideoGeneration.id)))
    hold_q = await db.execute(
        select(func.count(VideoGeneration.id)).where(
            VideoGeneration.status == GenerationStatus.MODERATION_HOLD
        )
    )
    events_q = await db.execute(select(func.count(ModerationEvent.id)))
    reviewed_q = await db.execute(
        select(func.count(ModerationEvent.id)).where(ModerationEvent.reviewer_decision.is_not(None))
    )
    overturned_q = await db.execute(
        select(func.count(ModerationEvent.id)).where(ModerationEvent.reviewer_decision == "approve")
    )
    cat_rows = await db.execute(
        select(ModerationEvent.categories).where(
            ModerationEvent.action.in_(["hold", "block", "ban"])
        ).limit(500)
    )
    buckets: dict[str, int] = {}
    for (cats,) in cat_rows.all():
        key = ",".join(cats or []) or "(none)"
        buckets[key] = buckets.get(key, 0) + 1
    total = int(total_q.scalar() or 0)
    hold = int(hold_q.scalar() or 0)
    reviewed = int(reviewed_q.scalar() or 0)
    overturned = int(overturned_q.scalar() or 0)
    return {
        "total_jobs": total,
        "hold_open": hold,
        "hold_rate": (hold / total) if total else 0.0,
        "events": int(events_q.scalar() or 0),
        "reviewed": reviewed,
        "overturn_rate": (overturned / reviewed) if reviewed else 0.0,
        "category_buckets": [
            {"categories": k.split(",") if k != "(none)" else [], "count": v}
            for k, v in sorted(buckets.items(), key=lambda kv: -kv[1])[:20]
        ],
    }


async def _load_hold(db: AsyncSession, generation_id: int) -> VideoGeneration:
    result = await db.execute(
        select(VideoGeneration)
        .options(selectinload(VideoGeneration.client))
        .where(VideoGeneration.id == generation_id)
    )
    generation = result.scalar_one_or_none()
    if not generation:
        raise HTTPException(status_code=404, detail="Generation not found")
    if generation.status != GenerationStatus.MODERATION_HOLD:
        raise HTTPException(status_code=400, detail="Задача не в очереди модерации")
    return generation


async def _mark_event(db: AsyncSession, generation_id: int, admin_id: int, decision: str) -> None:
    ev = await db.execute(
        select(ModerationEvent)
        .where(ModerationEvent.generation_id == generation_id)
        .order_by(ModerationEvent.id.desc())
        .limit(1)
    )
    event = ev.scalar_one_or_none()
    if event:
        event.reviewer_id = admin_id
        event.reviewer_decision = decision
        event.reviewed_at = datetime.utcnow()


@router.post("/moderation/{generation_id}/approve")
async def approve_hold(
    generation_id: int,
    background_tasks: BackgroundTasks,
    body: ReviewBody = ReviewBody(),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    generation = await _load_hold(db, generation_id)
    await _mark_event(db, generation_id, admin.id, "approve")
    generation.api_responses = generation.api_responses or {}
    generation.api_responses["moderation_override"] = True
    generation.api_responses["moderation_review"] = {
        "decision": "approve",
        "note": body.note,
        "by": admin.id,
        "at": datetime.utcnow().isoformat(),
    }
    generation.status = GenerationStatus.PENDING
    generation.error_message = None
    generation.progress_percent = 0
    kind = KIND_BY_MODE.get(generation.mode or "avatar", "avatar")
    await db.commit()
    requeue_generation(kind, generation.id, generation.client_id, background_tasks)
    return {"ok": True, "status": "pending"}


@router.post("/moderation/{generation_id}/reject")
async def reject_hold(
    generation_id: int,
    body: ReviewBody = ReviewBody(),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    generation = await _load_hold(db, generation_id)
    await _mark_event(db, generation_id, admin.id, "reject")
    generation.status = GenerationStatus.BLOCKED
    generation.error_message = "Отклонено модератором. Токен не списан."
    generation.api_responses = generation.api_responses or {}
    generation.api_responses["moderation_review"] = {
        "decision": "reject",
        "note": body.note,
        "by": admin.id,
        "at": datetime.utcnow().isoformat(),
    }
    await db.commit()
    return {"ok": True, "status": "blocked"}


@router.get("/moderation/complaints")
async def list_complaints(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    result = await db.execute(
        select(RightsComplaint).order_by(RightsComplaint.created_at.desc()).limit(100)
    )
    rows = result.scalars().all()
    return {
        "items": [
            {
                "id": r.id,
                "email": r.email,
                "source_url": r.source_url,
                "generation_id": r.generation_id,
                "message": r.message,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }


@router.post("/moderation/complaints/{complaint_id}")
async def resolve_complaint(
    complaint_id: int,
    body: ComplaintNote,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    result = await db.execute(select(RightsComplaint).where(RightsComplaint.id == complaint_id))
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Complaint not found")
    row.status = body.status or "resolved"
    row.admin_note = body.note
    row.resolved_at = datetime.utcnow()
    await db.commit()
    return {"ok": True}
