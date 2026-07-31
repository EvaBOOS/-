"""Pydantic schemas for trend radar."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TrendItemOut(BaseModel):
    id: int
    platform: str
    external_id: str
    title: Optional[str] = None
    url: Optional[str] = None
    niche: Optional[str] = None
    region: Optional[str] = None
    metrics: Optional[Dict[str, Any]] = None
    fetched_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class TrendsListResponse(BaseModel):
    items: List[TrendItemOut]
    meta: Dict[str, Any] = Field(default_factory=dict)


class TrendAnalyzeRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=1000)
    trend_item_id: Optional[int] = None


class TrendInsightOut(BaseModel):
    id: int
    client_id: Optional[int] = None
    trend_item_id: Optional[int] = None
    source_url: str
    status: str
    progress_percent: int = 0
    error_message: Optional[str] = None
    transcript_summary: Optional[str] = None
    hook_text: Optional[str] = None
    duration_sec: Optional[float] = None
    pace_wpm: Optional[float] = None
    style_guess: Optional[str] = None
    tips: Optional[List[str]] = None
    raw_llm: Optional[Dict[str, Any]] = None
    linked_generation_id: Optional[int] = None
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True
