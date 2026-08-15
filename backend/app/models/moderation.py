from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    JSON,
)
from sqlalchemy.sql import func

from app.db.session import Base


class ModerationEvent(Base):
    """Audit log for every gate verdict. Used for hold queue, appeals, false-positive stats."""

    __tablename__ = "moderation_events"

    id = Column(Integer, primary_key=True, index=True)
    generation_id = Column(Integer, ForeignKey("video_generations.id"), nullable=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    action = Column(String(20), nullable=False, index=True)
    categories = Column(JSON, nullable=True)
    stage = Column(String(20), nullable=True, index=True)
    reason = Column(Text, nullable=True)
    scores = Column(JSON, nullable=True)
    evidence = Column(JSON, nullable=True)

    appealed = Column(Boolean, default=False)
    appeal_text = Column(Text, nullable=True)
    appealed_at = Column(DateTime(timezone=True), nullable=True)

    reviewer_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewer_decision = Column(String(20), nullable=True)  # approve | reject
    reviewed_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class BlockedFileHash(Base):
    """SHA-256 of previously banned files. CSAE matches never store the file itself."""

    __tablename__ = "blocked_file_hashes"

    id = Column(Integer, primary_key=True, index=True)
    sha256 = Column(String(64), unique=True, nullable=False, index=True)
    category = Column(String(20), nullable=False, default="CSAE")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class RightsComplaint(Base):
    """Copyright / rights-holder takedown request from the public form."""

    __tablename__ = "rights_complaints"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), nullable=False)
    source_url = Column(String(1000), nullable=True)
    generation_id = Column(Integer, nullable=True)
    message = Column(Text, nullable=False)
    status = Column(String(20), default="pending", nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    admin_note = Column(Text, nullable=True)
