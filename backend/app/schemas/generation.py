from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from app.models.generation import GenerationStatus


class GenerationCreate(BaseModel):
    original_text: str = Field(..., min_length=10, max_length=5000)
    target_language: str = Field("ru", min_length=2, max_length=10)


class GenerationStatusUpdate(BaseModel):
    status: GenerationStatus
    error_message: Optional[str] = None
    progress_percent: Optional[int] = Field(None, ge=0, le=100)


class GenerationResponse(BaseModel):
    id: int
    client_id: int
    original_text: str
    target_language: str
    generated_script: Optional[str] = None
    status: GenerationStatus
    error_message: Optional[str] = None
    progress_percent: int
    final_video_path: Optional[str] = None
    duration_seconds: Optional[int] = None
    file_size_bytes: Optional[int] = None
    credit_deducted: bool
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class GenerationListResponse(BaseModel):
    items: List[GenerationResponse]
    total: int
    page: int
    per_page: int
    pages: int
