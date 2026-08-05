"""Trend radar models: discovered items + study insights."""
from __future__ import annotations

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    ForeignKey,
    Float,
    JSON,
    Boolean,
)
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base


class TrendItem(Base):
    """A discovered trending video / hashtag entry (metadata only)."""

    __tablename__ = "trend_items"

    id = Column(Integer, primary_key=True, index=True)
    # youtube | tiktok | instagram | vk
    platform = Column(String(32), nullable=False, index=True)
    external_id = Column(String(128), nullable=False, index=True)
    title = Column(String(500), nullable=True)
    url = Column(String(1000), nullable=True)
    niche = Column(String(120), nullable=True, index=True)
    region = Column(String(16), nullable=True, index=True)
    # views, likes, rank, hashtag, channel, thumb, …
    metrics = Column(MutableDict.as_mutable(JSON), nullable=True)
    fetched_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    insights = relationship(
        "TrendInsight",
        back_populates="trend_item",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<TrendItem {self.platform}:{self.external_id}>"


class TrendInsight(Base):
    """LLM study result for a trend URL (may belong to a client)."""

    __tablename__ = "trend_insights"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=True, index=True)
    trend_item_id = Column(Integer, ForeignKey("trend_items.id"), nullable=True, index=True)

    source_url = Column(String(1000), nullable=False)
    status = Column(String(32), default="pending", index=True)  # pending|processing|completed|failed
    progress_percent = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)

    # Local paths after ingest
    source_video_path = Column(String(500), nullable=True)
    transcript_summary = Column(Text, nullable=True)
    hook_text = Column(String(300), nullable=True)
    duration_sec = Column(Float, nullable=True)
    pace_wpm = Column(Float, nullable=True)
    style_guess = Column(String(40), nullable=True)
    tips = Column(MutableList.as_mutable(JSON), nullable=True)
    hashtags = Column(MutableList.as_mutable(JSON), nullable=True)
    raw_llm = Column(MutableDict.as_mutable(JSON), nullable=True)

    credit_deducted = Column(Boolean, default=False)
    linked_generation_id = Column(Integer, ForeignKey("video_generations.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    trend_item = relationship("TrendItem", back_populates="insights")

    def __repr__(self):
        return f"<TrendInsight {self.id} {self.status}>"
