from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Enum as SQLEnum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.session import Base
import enum


class SubscriptionPlan(str, enum.Enum):
    BASIC = "basic"      # 15 videos/month
    STANDARD = "standard"  # 30 videos/month
    PREMIUM = "premium"   # 60 videos/month


PLAN_LIMITS = {
    SubscriptionPlan.BASIC: 15,
    SubscriptionPlan.STANDARD: 30,
    SubscriptionPlan.PREMIUM: 60
}


class Client(Base):
    __tablename__ = "clients"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    company_name = Column(String(255), nullable=True)
    
    # Subscription & Credits
    subscription_plan = Column(SQLEnum(SubscriptionPlan), default=SubscriptionPlan.BASIC)
    credits_remaining = Column(Integer, default=15)
    credits_used_this_month = Column(Integer, default=0)
    billing_cycle_start = Column(DateTime(timezone=True), server_default=func.now())
    
    # AI Voice & Avatar Settings
    elevenlabs_voice_id = Column(String(100), nullable=True)
    heygen_avatar_id = Column(String(100), nullable=True)
    custom_voice_clone_id = Column(String(100), nullable=True)
    
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    user = relationship("User", backref="client_profile")
    branding = relationship("ClientBranding", back_populates="client", uselist=False, cascade="all, delete-orphan")
    generations = relationship("VideoGeneration", back_populates="client", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Client {self.company_name or self.id}>"


class ClientBranding(Base):
    __tablename__ = "client_branding"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), unique=True, nullable=False)
    
    # Watermark/Logo
    watermark_path = Column(String(500), nullable=True)
    watermark_position = Column(String(50), default="bottom_right")  # top_left, top_right, bottom_left, bottom_right
    watermark_opacity = Column(Integer, default=80)  # 0-100
    watermark_scale = Column(Integer, default=15)  # percentage of video width
    
    # Subtitles
    subtitle_font_name = Column(String(100), default="Arial")
    subtitle_font_path = Column(String(500), nullable=True)
    subtitle_font_size = Column(Integer, default=48)
    subtitle_font_color = Column(String(20), default="#FFFFFF")
    subtitle_bg_color = Column(String(20), nullable=True)  # Optional background
    subtitle_position = Column(String(50), default="bottom")  # top, center, bottom
    
    # Brand Colors (for future use)
    primary_color = Column(String(20), nullable=True)
    secondary_color = Column(String(20), nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    client = relationship("Client", back_populates="branding")

    def __repr__(self):
        return f"<ClientBranding for client {self.client_id}>"
