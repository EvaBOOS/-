from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Enum as SQLEnum, JSON
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.session import Base
import enum


class GenerationMode(str, enum.Enum):
    AVATAR = "avatar"
    VIRAL_EDIT = "viral_edit"
    AI_CLIPS = "ai_clips"


class GenerationStatus(str, enum.Enum):
    PENDING = "pending"
    SCRIPT_GENERATION = "script_generation"
    VOICE_SYNTHESIS = "voice_synthesis"
    AVATAR_GENERATION = "avatar_generation"
    TRANSCRIPTION = "transcription"
    VIRAL_EDIT = "viral_edit"
    CLIPPING = "clipping"
    VIDEO_PROCESSING = "video_processing"
    COMPLETED = "completed"
    FAILED = "failed"


class VideoGeneration(Base):
    __tablename__ = "video_generations"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)

    # avatar | viral_edit
    mode = Column(String(32), default=GenerationMode.AVATAR.value)

    # Input
    original_text = Column(Text, nullable=False)
    target_language = Column(String(10), default="ru")  # ISO 639-1 code
    source_video_path = Column(String(500), nullable=True)

    # Generated Content
    generated_script = Column(Text, nullable=True)

    # Processing Status
    status = Column(SQLEnum(GenerationStatus), default=GenerationStatus.PENDING)
    error_message = Column(Text, nullable=True)
    progress_percent = Column(Integer, default=0)

    # Generated Files
    audio_path = Column(String(500), nullable=True)
    avatar_video_path = Column(String(500), nullable=True)
    final_video_path = Column(String(500), nullable=True)

    # Metadata
    duration_seconds = Column(Integer, nullable=True)
    file_size_bytes = Column(Integer, nullable=True)

    # API Response Data (for debugging) — MutableDict so in-place updates persist
    api_responses = Column(MutableDict.as_mutable(JSON), nullable=True)

    # Credit tracking
    credit_deducted = Column(Boolean, default=False)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    client = relationship("Client", back_populates="generations")

    def __repr__(self):
        return f"<VideoGeneration {self.id} - {self.status}>"
