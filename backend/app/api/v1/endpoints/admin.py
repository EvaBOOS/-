from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from typing import List, Optional
import os
import uuid
import aiofiles

from app.api.deps import get_db, get_current_admin
from app.core.security import get_password_hash
from app.core.config import settings
from app.models.user import User, UserRole
from app.models.client import Client, ClientBranding, SubscriptionPlan, PLAN_LIMITS
from app.models.generation import VideoGeneration
from app.schemas.client import (
    ClientCreate, ClientUpdate, ClientResponse, ClientWithBranding,
    ClientBrandingUpdate, ClientBrandingResponse
)

router = APIRouter()


@router.get("/clients", response_model=List[ClientWithBranding])
async def list_clients(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """List all clients with their branding settings."""
    result = await db.execute(
        select(Client)
        .options(selectinload(Client.branding), selectinload(Client.user))
        .offset(skip)
        .limit(limit)
        .order_by(Client.created_at.desc())
    )
    clients = result.scalars().all()
    
    response = []
    for client in clients:
        client_data = ClientWithBranding.model_validate(client)
        client_data.user_email = client.user.email if client.user else None
        client_data.user_full_name = client.user.full_name if client.user else None
        response.append(client_data)
    
    return response


@router.post("/clients", response_model=ClientWithBranding, status_code=status.HTTP_201_CREATED)
async def create_client(
    client_data: ClientCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """Create a new client account with user credentials."""
    # Check if user email already exists
    result = await db.execute(
        select(User).where(User.email == client_data.user_email)
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User with this email already exists"
        )
    
    # Create user
    user = User(
        email=client_data.user_email,
        hashed_password=get_password_hash(client_data.user_password),
        full_name=client_data.user_full_name,
        role=UserRole.CLIENT
    )
    db.add(user)
    await db.flush()
    
    # Create client profile
    plan_limit = PLAN_LIMITS.get(client_data.subscription_plan, 15)
    client = Client(
        user_id=user.id,
        company_name=client_data.company_name,
        subscription_plan=client_data.subscription_plan,
        credits_remaining=plan_limit,
        elevenlabs_voice_id=client_data.elevenlabs_voice_id,
        heygen_avatar_id=client_data.heygen_avatar_id,
        custom_voice_clone_id=client_data.custom_voice_clone_id
    )
    db.add(client)
    await db.flush()
    
    # Create default branding
    branding = ClientBranding(client_id=client.id)
    db.add(branding)
    
    await db.commit()
    await db.refresh(client)
    
    # Reload with relationships
    result = await db.execute(
        select(Client)
        .options(selectinload(Client.branding), selectinload(Client.user))
        .where(Client.id == client.id)
    )
    client = result.scalar_one()
    
    response = ClientWithBranding.model_validate(client)
    response.user_email = user.email
    response.user_full_name = user.full_name
    
    return response


@router.get("/clients/{client_id}", response_model=ClientWithBranding)
async def get_client(
    client_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """Get client details by ID."""
    result = await db.execute(
        select(Client)
        .options(selectinload(Client.branding), selectinload(Client.user))
        .where(Client.id == client_id)
    )
    client = result.scalar_one_or_none()
    
    if not client:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Client not found"
        )
    
    response = ClientWithBranding.model_validate(client)
    response.user_email = client.user.email if client.user else None
    response.user_full_name = client.user.full_name if client.user else None
    
    return response


@router.patch("/clients/{client_id}", response_model=ClientWithBranding)
async def update_client(
    client_id: int,
    update_data: ClientUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """Update client settings including subscription and AI settings."""
    result = await db.execute(
        select(Client)
        .options(selectinload(Client.branding), selectinload(Client.user))
        .where(Client.id == client_id)
    )
    client = result.scalar_one_or_none()
    
    if not client:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Client not found"
        )
    
    update_dict = update_data.model_dump(exclude_unset=True)
    for field, value in update_dict.items():
        setattr(client, field, value)
    
    await db.commit()
    await db.refresh(client)
    
    response = ClientWithBranding.model_validate(client)
    response.user_email = client.user.email if client.user else None
    response.user_full_name = client.user.full_name if client.user else None
    
    return response


@router.post("/clients/{client_id}/add-credits")
async def add_credits(
    client_id: int,
    credits: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """Add credits to client's balance."""
    result = await db.execute(select(Client).where(Client.id == client_id))
    client = result.scalar_one_or_none()
    
    if not client:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Client not found"
        )
    
    client.credits_remaining += credits
    await db.commit()
    
    return {
        "message": f"Added {credits} credits",
        "new_balance": client.credits_remaining
    }


@router.post("/clients/{client_id}/reset-cycle")
async def reset_billing_cycle(
    client_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """Reset client's monthly billing cycle and restore credits."""
    result = await db.execute(select(Client).where(Client.id == client_id))
    client = result.scalar_one_or_none()
    
    if not client:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Client not found"
        )
    
    plan_limit = PLAN_LIMITS.get(client.subscription_plan, 15)
    client.credits_remaining = plan_limit
    client.credits_used_this_month = 0
    
    from datetime import datetime
    client.billing_cycle_start = datetime.utcnow()
    
    await db.commit()
    
    return {
        "message": "Billing cycle reset",
        "credits_remaining": client.credits_remaining
    }


@router.patch("/clients/{client_id}/branding", response_model=ClientBrandingResponse)
async def update_branding(
    client_id: int,
    branding_data: ClientBrandingUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """Update client branding settings."""
    result = await db.execute(
        select(ClientBranding).where(ClientBranding.client_id == client_id)
    )
    branding = result.scalar_one_or_none()
    
    if not branding:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Client branding not found"
        )
    
    update_dict = branding_data.model_dump(exclude_unset=True)
    for field, value in update_dict.items():
        setattr(branding, field, value)
    
    await db.commit()
    await db.refresh(branding)
    
    return branding


@router.post("/clients/{client_id}/watermark")
async def upload_watermark(
    client_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """Upload watermark/logo for client."""
    result = await db.execute(
        select(ClientBranding).where(ClientBranding.client_id == client_id)
    )
    branding = result.scalar_one_or_none()
    
    if not branding:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Client branding not found"
        )
    
    # Validate file type
    allowed_types = ["image/png", "image/jpeg", "image/webp"]
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file type. Allowed: PNG, JPEG, WebP"
        )
    
    # Save file
    ext = file.filename.split(".")[-1] if file.filename else "png"
    filename = f"{uuid.uuid4()}.{ext}"
    filepath = os.path.join(settings.UPLOAD_DIR, "watermarks", filename)
    
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    
    async with aiofiles.open(filepath, "wb") as f:
        content = await file.read()
        await f.write(content)
    
    # Update branding
    branding.watermark_path = filepath
    await db.commit()
    
    return {"message": "Watermark uploaded", "path": filepath}


@router.post("/clients/{client_id}/font")
async def upload_font(
    client_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """Upload custom font for client subtitles."""
    result = await db.execute(
        select(ClientBranding).where(ClientBranding.client_id == client_id)
    )
    branding = result.scalar_one_or_none()
    
    if not branding:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Client branding not found"
        )
    
    # Validate file type
    allowed_types = ["font/ttf", "font/otf", "application/x-font-ttf", 
                     "application/x-font-otf", "application/octet-stream"]
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file type. Allowed: TTF, OTF"
        )
    
    # Save file
    ext = file.filename.split(".")[-1] if file.filename else "ttf"
    filename = f"{uuid.uuid4()}.{ext}"
    filepath = os.path.join(settings.UPLOAD_DIR, "fonts", filename)
    
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    
    async with aiofiles.open(filepath, "wb") as f:
        content = await file.read()
        await f.write(content)
    
    # Update branding
    branding.subtitle_font_path = filepath
    await db.commit()
    
    return {"message": "Font uploaded", "path": filepath}


@router.get("/assets/fonts")
async def list_library_fonts(
    admin: User = Depends(get_current_admin),
):
    """Curated Google Fonts for client branding (auto-downloaded)."""
    from app.services.assets.library_service import AssetLibraryService
    return {"fonts": AssetLibraryService().list_fonts()}


@router.post("/clients/{client_id}/branding/library-font")
async def apply_library_font(
    client_id: int,
    font_id: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Download a Google Font into cache and set it as the client's subtitle font."""
    from app.services.assets.library_service import AssetLibraryService

    result = await db.execute(
        select(ClientBranding).where(ClientBranding.client_id == client_id)
    )
    branding = result.scalar_one_or_none()
    if not branding:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Client branding not found",
        )

    lib = AssetLibraryService()
    meta = lib.get_font_meta(font_id)
    if not meta:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown font_id: {font_id}",
        )

    family, fonts_dir, font_file = await lib.ensure_font_file(font_id)
    branding.subtitle_font_name = family
    branding.subtitle_font_path = font_file
    await db.commit()
    await db.refresh(branding)

    return {
        "message": f"Font {family} applied",
        "font_id": font_id,
        "family": family,
        "path": font_file,
        "fonts_dir": fonts_dir,
    }


@router.get("/stats")
async def get_stats(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """Get platform statistics."""
    # Total clients
    client_count = await db.execute(select(func.count(Client.id)))
    total_clients = client_count.scalar()
    
    # Total generations
    gen_count = await db.execute(select(func.count(VideoGeneration.id)))
    total_generations = gen_count.scalar()
    
    # Successful generations
    from app.models.generation import GenerationStatus
    success_count = await db.execute(
        select(func.count(VideoGeneration.id))
        .where(VideoGeneration.status == GenerationStatus.COMPLETED)
    )
    successful_generations = success_count.scalar()
    
    # Active clients
    active_count = await db.execute(
        select(func.count(Client.id)).where(Client.is_active == True)
    )
    active_clients = active_count.scalar()
    
    return {
        "total_clients": total_clients,
        "active_clients": active_clients,
        "total_generations": total_generations,
        "successful_generations": successful_generations,
        "success_rate": round(successful_generations / total_generations * 100, 2) if total_generations > 0 else 0
    }
