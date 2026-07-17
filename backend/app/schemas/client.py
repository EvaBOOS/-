from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from app.models.client import SubscriptionPlan


class ClientBrandingBase(BaseModel):
    watermark_position: str = "bottom_right"
    watermark_opacity: int = Field(80, ge=0, le=100)
    watermark_scale: int = Field(15, ge=5, le=50)
    subtitle_font_name: str = "Arial"
    subtitle_font_size: int = Field(48, ge=12, le=120)
    subtitle_font_color: str = "#FFFFFF"
    subtitle_bg_color: Optional[str] = None
    subtitle_position: str = "bottom"
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None


class ClientBrandingCreate(ClientBrandingBase):
    pass


class ClientBrandingUpdate(BaseModel):
    watermark_position: Optional[str] = None
    watermark_opacity: Optional[int] = Field(None, ge=0, le=100)
    watermark_scale: Optional[int] = Field(None, ge=5, le=50)
    subtitle_font_name: Optional[str] = None
    subtitle_font_size: Optional[int] = Field(None, ge=12, le=120)
    subtitle_font_color: Optional[str] = None
    subtitle_bg_color: Optional[str] = None
    subtitle_position: Optional[str] = None
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None


class ClientBrandingResponse(ClientBrandingBase):
    id: int
    client_id: int
    watermark_path: Optional[str] = None
    subtitle_font_path: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ClientBase(BaseModel):
    company_name: Optional[str] = None
    subscription_plan: SubscriptionPlan = SubscriptionPlan.BASIC
    elevenlabs_voice_id: Optional[str] = None
    heygen_avatar_id: Optional[str] = None
    custom_voice_clone_id: Optional[str] = None


class ClientCreate(ClientBase):
    user_email: str
    user_password: str = Field(..., min_length=8)
    user_full_name: Optional[str] = None


class ClientUpdate(BaseModel):
    company_name: Optional[str] = None
    subscription_plan: Optional[SubscriptionPlan] = None
    credits_remaining: Optional[int] = Field(None, ge=0)
    elevenlabs_voice_id: Optional[str] = None
    heygen_avatar_id: Optional[str] = None
    custom_voice_clone_id: Optional[str] = None
    is_active: Optional[bool] = None


class ClientResponse(ClientBase):
    id: int
    user_id: int
    credits_remaining: int
    credits_used_this_month: int
    billing_cycle_start: datetime
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ClientWithBranding(ClientResponse):
    branding: Optional[ClientBrandingResponse] = None
    user_email: Optional[str] = None
    user_full_name: Optional[str] = None

    class Config:
        from_attributes = True
