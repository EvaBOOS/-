from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from typing import Optional
import os
import math

from app.api.deps import get_db, get_current_client, get_current_user
from app.models.client import Client
from app.models.generation import VideoGeneration, GenerationStatus
from app.models.user import User
from app.schemas.generation import GenerationCreate, GenerationResponse, GenerationListResponse
from app.schemas.client import ClientResponse, ClientBrandingResponse
from app.services.pipeline import VideoGenerationPipeline

router = APIRouter()


@router.get("/profile", response_model=ClientResponse)
async def get_profile(
    client: Client = Depends(get_current_client)
):
    """Get current client's profile."""
    return client


@router.get("/branding", response_model=ClientBrandingResponse)
async def get_branding(
    client: Client = Depends(get_current_client),
    db: AsyncSession = Depends(get_db)
):
    """Get current client's branding settings."""
    result = await db.execute(
        select(Client)
        .options(selectinload(Client.branding))
        .where(Client.id == client.id)
    )
    client_with_branding = result.scalar_one()
    
    if not client_with_branding.branding:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branding settings not found"
        )
    
    return client_with_branding.branding


@router.post("/generate", response_model=GenerationResponse, status_code=status.HTTP_201_CREATED)
async def create_generation(
    generation_data: GenerationCreate,
    background_tasks: BackgroundTasks,
    client: Client = Depends(get_current_client),
    db: AsyncSession = Depends(get_db)
):
    """
    Submit a new video generation request.
    
    The text will be processed through the AI pipeline:
    1. GPT-4o generates viral script
    2. ElevenLabs synthesizes voice
    3. HeyGen creates avatar video with lip sync
    4. FFmpeg adds subtitles and watermark
    """
    # Check credits
    if client.credits_remaining <= 0:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="No credits remaining. Please contact admin to add more credits."
        )
    
    # Create generation record
    generation = VideoGeneration(
        client_id=client.id,
        original_text=generation_data.original_text,
        target_language=generation_data.target_language,
        status=GenerationStatus.PENDING
    )
    db.add(generation)
    await db.commit()
    await db.refresh(generation)
    
    # Start background processing
    background_tasks.add_task(
        process_video_generation,
        generation.id,
        client.id
    )
    
    return generation


async def process_video_generation(generation_id: int, client_id: int):
    """Background task for video generation pipeline."""
    from app.db.session import AsyncSessionLocal
    
    async with AsyncSessionLocal() as db:
        pipeline = VideoGenerationPipeline(db)
        await pipeline.process(generation_id, client_id)


@router.get("/generations", response_model=GenerationListResponse)
async def list_generations(
    page: int = 1,
    per_page: int = 20,
    status_filter: Optional[GenerationStatus] = None,
    client: Client = Depends(get_current_client),
    db: AsyncSession = Depends(get_db)
):
    """List all video generations for the current client."""
    # Build query
    query = select(VideoGeneration).where(VideoGeneration.client_id == client.id)
    count_query = select(func.count(VideoGeneration.id)).where(
        VideoGeneration.client_id == client.id
    )
    
    if status_filter:
        query = query.where(VideoGeneration.status == status_filter)
        count_query = count_query.where(VideoGeneration.status == status_filter)
    
    # Get total count
    total_result = await db.execute(count_query)
    total = total_result.scalar()
    
    # Get paginated results
    offset = (page - 1) * per_page
    result = await db.execute(
        query.order_by(VideoGeneration.created_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    generations = result.scalars().all()
    
    return GenerationListResponse(
        items=generations,
        total=total,
        page=page,
        per_page=per_page,
        pages=math.ceil(total / per_page) if total > 0 else 0
    )


@router.get("/generations/{generation_id}", response_model=GenerationResponse)
async def get_generation(
    generation_id: int,
    client: Client = Depends(get_current_client),
    db: AsyncSession = Depends(get_db)
):
    """Get details of a specific generation."""
    result = await db.execute(
        select(VideoGeneration)
        .where(
            VideoGeneration.id == generation_id,
            VideoGeneration.client_id == client.id
        )
    )
    generation = result.scalar_one_or_none()
    
    if not generation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Generation not found"
        )
    
    return generation


@router.get("/generations/{generation_id}/download")
async def download_video(
    generation_id: int,
    client: Client = Depends(get_current_client),
    db: AsyncSession = Depends(get_db)
):
    """Download the generated video file."""
    result = await db.execute(
        select(VideoGeneration)
        .where(
            VideoGeneration.id == generation_id,
            VideoGeneration.client_id == client.id
        )
    )
    generation = result.scalar_one_or_none()
    
    if not generation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Generation not found"
        )
    
    if generation.status != GenerationStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Video is not ready. Current status: {generation.status}"
        )
    
    if not generation.final_video_path or not os.path.exists(generation.final_video_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video file not found"
        )
    
    return FileResponse(
        generation.final_video_path,
        media_type="video/mp4",
        filename=f"video_{generation_id}.mp4"
    )


@router.delete("/generations/{generation_id}")
async def cancel_generation(
    generation_id: int,
    client: Client = Depends(get_current_client),
    db: AsyncSession = Depends(get_db)
):
    """Cancel a pending generation request."""
    result = await db.execute(
        select(VideoGeneration)
        .where(
            VideoGeneration.id == generation_id,
            VideoGeneration.client_id == client.id
        )
    )
    generation = result.scalar_one_or_none()
    
    if not generation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Generation not found"
        )
    
    if generation.status not in [GenerationStatus.PENDING, GenerationStatus.FAILED]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only cancel pending or failed generations"
        )
    
    await db.delete(generation)
    await db.commit()
    
    return {"message": "Generation cancelled"}


@router.get("/credits")
async def get_credits(
    client: Client = Depends(get_current_client)
):
    """Get current credit balance."""
    return {
        "credits_remaining": client.credits_remaining,
        "credits_used_this_month": client.credits_used_this_month,
        "subscription_plan": client.subscription_plan,
        "billing_cycle_start": client.billing_cycle_start
    }
