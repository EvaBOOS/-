from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from typing import Optional
import os
import math
import uuid
import aiofiles

from app.api.deps import get_db, get_current_client, get_current_user
from app.core.config import settings
from app.models.client import Client
from app.models.generation import VideoGeneration, GenerationStatus, GenerationMode
from app.models.user import User
from app.schemas.generation import GenerationCreate, GenerationResponse, GenerationListResponse
from app.schemas.client import ClientResponse, ClientBrandingResponse
from app.services.pipeline import VideoGenerationPipeline
from app.services.viral_edit_pipeline import ViralEditPipeline
from app.services.clips_pipeline import AiClipsPipeline
from app.services.jobs import enqueue_job
from app.services.media_ingest import MediaIngestError, validate_public_http_url

router = APIRouter()


async def _save_upload_or_url(
    *,
    file: Optional[UploadFile],
    source_url: str,
    upload_subdir: str,
    client_id: int,
    max_bytes: int,
) -> tuple[Optional[str], str, Optional[str]]:
    """
    Persist uploaded file now, or validate URL for background download.
    Returns (saved_path|None, display_name, validated_url|None).
    """
    url = (source_url or "").strip()
    has_file = bool(file is not None and (file.filename or "").strip())

    if has_file and url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Укажите либо файл, либо ссылку — не оба сразу",
        )
    if not has_file and not url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Загрузите видеофайл или вставьте ссылку",
        )

    if url:
        try:
            safe = validate_public_http_url(url)
        except MediaIngestError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        if not settings.LINK_INGEST_ENABLED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Импорт по ссылке отключён",
            )
        # Short label for history; real title filled after download in worker
        label = safe if len(safe) <= 120 else safe[:117] + "…"
        return None, label, safe

    upload_dir = os.path.join(settings.UPLOAD_DIR, upload_subdir, str(client_id))
    os.makedirs(upload_dir, exist_ok=True)

    filename = file.filename or "upload.mp4"
    ext = os.path.splitext(filename)[1].lower() or ".mp4"
    if ext not in {".mp4", ".mov", ".webm", ".mkv", ".m4v"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Supported formats: mp4, mov, webm, mkv",
        )

    saved_name = f"{uuid.uuid4()}{ext}"
    saved_path = os.path.join(upload_dir, saved_name)
    size = 0
    try:
        async with aiofiles.open(saved_path, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Max upload size is {max_bytes // (1024 * 1024)}MB",
                    )
                await out.write(chunk)
    except HTTPException:
        try:
            os.remove(saved_path)
        except OSError:
            pass
        raise
    except Exception:
        try:
            os.remove(saved_path)
        except OSError:
            pass
        raise

    return saved_path, filename, None


def _normalize_font_id(font_id: str) -> str:
    font_norm = (font_id or "").strip().lower()
    if font_norm in {"", "auto", "default"}:
        return ""
    from app.services.assets.library_service import AssetLibraryService

    if not AssetLibraryService().get_font_meta(font_norm):
        return ""
    return font_norm


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
        mode=GenerationMode.AVATAR.value,
        original_text=generation_data.original_text,
        target_language=generation_data.target_language,
        status=GenerationStatus.PENDING
    )
    db.add(generation)
    await db.commit()
    await db.refresh(generation)
    
    # Start background processing (Celery in prod, BackgroundTasks locally)
    enqueue_job(
        background_tasks,
        "avatar",
        generation.id,
        client.id,
        process_video_generation,
    )
    
    return generation


@router.post("/viral-edit", response_model=GenerationResponse, status_code=status.HTTP_201_CREATED)
async def create_viral_edit(
    background_tasks: BackgroundTasks,
    file: Optional[UploadFile] = File(None),
    source_url: str = Form(""),
    language: str = Form("ru"),
    style: str = Form("dynamic"),
    format: str = Form("9:16"),
    dub_language: str = Form(""),
    font_id: str = Form(""),
    voiceover_text: str = Form(""),
    client: Client = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload a talking-head / raw clip (or paste a public URL) and run the viral-edit pipeline:
    Whisper (optional) → optional voiceover/dub → format → zooms/B-roll/hook/music → karaoke → virality score.
    Silent clips (no speech) are supported; pass voiceover_text to add TTS later.
    """
    if client.credits_remaining <= 0:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="No credits remaining. Please contact admin to add more credits.",
        )

    from app.services.video.edit_styles import VALID_STYLES
    style_norm = (style or "dynamic").strip().lower()
    if style_norm not in VALID_STYLES:
        style_norm = "dynamic"

    format_norm = (format or "9:16").strip()
    if format_norm not in {"9:16", "1:1", "16:9"}:
        format_norm = "9:16"

    dub_norm = (dub_language or "").strip().lower()
    if dub_norm in {"", "same", "none", "original"}:
        dub_norm = ""

    font_norm = _normalize_font_id(font_id)
    vo_norm = (voiceover_text or "").strip()[:4000]

    saved_path, display_name, validated_url = await _save_upload_or_url(
        file=file,
        source_url=source_url,
        upload_subdir="viral",
        client_id=client.id,
        max_bytes=settings.MAX_VIRAL_UPLOAD_SIZE,
    )

    generation = VideoGeneration(
        client_id=client.id,
        mode=GenerationMode.VIRAL_EDIT.value,
        original_text=f"[viral_edit] {display_name}",
        target_language=(language or "ru")[:10],
        source_video_path=saved_path,
        status=GenerationStatus.PENDING,
        api_responses={
            "edit_style": style_norm,
            "edit_format": format_norm,
            "dub_language": dub_norm,
            "font_id": font_norm or None,
            "source_url": validated_url,
            "voiceover_text": vo_norm or None,
        },
    )
    db.add(generation)
    await db.commit()
    await db.refresh(generation)

    enqueue_job(
        background_tasks,
        "viral",
        generation.id,
        client.id,
        process_viral_edit,
    )
    return generation


@router.post("/clips", response_model=GenerationResponse, status_code=status.HTTP_201_CREATED)
async def create_ai_clips(
    background_tasks: BackgroundTasks,
    file: Optional[UploadFile] = File(None),
    source_url: str = Form(""),
    language: str = Form("ru"),
    max_clips: int = Form(5),
    font_id: str = Form(""),
    client: Client = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload a long video (or paste a public URL); AI finds highlight moments and exports Shorts clips.
    """
    if client.credits_remaining <= 0:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="No credits remaining. Please contact admin to add more credits.",
        )

    clips_n = max(1, min(int(max_clips or 5), settings.AI_CLIPS_MAX_COUNT))
    font_norm = _normalize_font_id(font_id)

    saved_path, display_name, validated_url = await _save_upload_or_url(
        file=file,
        source_url=source_url,
        upload_subdir="clips",
        client_id=client.id,
        max_bytes=settings.MAX_CLIPS_UPLOAD_SIZE,
    )

    generation = VideoGeneration(
        client_id=client.id,
        mode=GenerationMode.AI_CLIPS.value,
        original_text=f"[ai_clips] {display_name}",
        target_language=(language or "ru")[:10],
        source_video_path=saved_path,
        status=GenerationStatus.PENDING,
        api_responses={
            "max_clips": clips_n,
            "font_id": font_norm or None,
            "source_url": validated_url,
        },
    )
    db.add(generation)
    await db.commit()
    await db.refresh(generation)

    enqueue_job(
        background_tasks,
        "clips",
        generation.id,
        client.id,
        process_ai_clips,
    )
    return generation


async def process_video_generation(generation_id: int, client_id: int):
    """Background task for video generation pipeline."""
    from app.db.session import AsyncSessionLocal
    
    async with AsyncSessionLocal() as db:
        pipeline = VideoGenerationPipeline(db)
        await pipeline.process(generation_id, client_id)


async def process_viral_edit(generation_id: int, client_id: int):
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        pipeline = ViralEditPipeline(db)
        await pipeline.process(generation_id, client_id)


async def process_ai_clips(generation_id: int, client_id: int):
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        pipeline = AiClipsPipeline(db)
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


@router.get("/generations/{generation_id}/clips/{clip_index}/download")
async def download_clip(
    generation_id: int,
    clip_index: int,
    client: Client = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """Download one AI clip from a completed clips job (1-based index)."""
    result = await db.execute(
        select(VideoGeneration)
        .where(
            VideoGeneration.id == generation_id,
            VideoGeneration.client_id == client.id,
        )
    )
    generation = result.scalar_one_or_none()
    if not generation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Generation not found")
    if generation.status != GenerationStatus.COMPLETED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Clips are not ready")

    paths = (generation.api_responses or {}).get("clips_abs") or []
    if clip_index < 1 or clip_index > len(paths):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clip not found")
    path = paths[clip_index - 1]
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clip file missing")

    return FileResponse(
        path,
        media_type="video/mp4",
        filename=f"clip_{generation_id}_{clip_index}.mp4",
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
    """Get current credit balance and plan watermark policy."""
    from app.services.branding_watermark import plan_watermark_policy

    return {
        "credits_remaining": client.credits_remaining,
        "credits_used_this_month": client.credits_used_this_month,
        "subscription_plan": client.subscription_plan,
        "billing_cycle_start": client.billing_cycle_start,
        "watermark_policy": plan_watermark_policy(client.subscription_plan),
    }


@router.get("/assets/status")
async def assets_status(
    client: Client = Depends(get_current_client),
):
    """What the remote asset library can provide (stickers/fonts/photos)."""
    from app.services.assets.library_service import AssetLibraryService
    return AssetLibraryService().status()


@router.get("/assets/fonts")
async def list_asset_fonts(
    client: Client = Depends(get_current_client),
):
    """Library fonts (Google + local OFL packs) for subtitles / branding."""
    from app.services.assets.library_service import AssetLibraryService
    return {"fonts": AssetLibraryService().list_fonts()}


@router.get("/assets/fonts/{font_id}/file")
async def get_library_font_file(
    font_id: str,
    client: Client = Depends(get_current_client),
):
    """Serve a catalog font file for UI preview (@font-face) and rendering."""
    from app.services.assets.library_service import AssetLibraryService

    lib = AssetLibraryService()
    meta = lib.get_font_meta(font_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Font not found")
    try:
        family, _fonts_dir, path = await lib.ensure_font_file(font_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Font unavailable: {e}") from e
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Font file missing")

    ext = os.path.splitext(path)[1].lower()
    media = "font/otf" if ext == ".otf" else "font/ttf"
    return FileResponse(
        path,
        media_type=media,
        filename=os.path.basename(path),
        headers={
            "Cache-Control": "public, max-age=86400",
            "X-Font-Family": family,
        },
    )


@router.post("/branding/library-font")
async def apply_library_font(
    font_id: str = Form(...),
    client: Client = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """Pick a catalog font as the client's default subtitle font."""
    from app.services.assets.library_service import AssetLibraryService
    from app.models.client import ClientBranding

    lib = AssetLibraryService()
    meta = lib.get_font_meta(font_id)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Unknown font_id: {font_id}")

    family, fonts_dir, font_file = await lib.ensure_font_file(font_id)

    result = await db.execute(
        select(Client)
        .options(selectinload(Client.branding))
        .where(Client.id == client.id)
    )
    row = result.scalar_one()
    branding = row.branding
    if not branding:
        branding = ClientBranding(client_id=client.id)
        db.add(branding)

    branding.subtitle_font_name = family
    branding.subtitle_font_path = font_file
    await db.commit()
    await db.refresh(branding)
    return {
        "message": "Font applied",
        "font_id": font_id,
        "family": family,
        "path": font_file,
        "fonts_dir": fonts_dir,
        "branding": {
            "subtitle_font_name": branding.subtitle_font_name,
            "subtitle_font_path": branding.subtitle_font_path,
        },
    }


@router.post("/branding/font")
async def upload_client_font(
    file: UploadFile = File(...),
    family_name: str = Form(""),
    client: Client = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """Upload a custom TTF/OTF font for this client's subtitles."""
    from app.models.client import ClientBranding

    filename = file.filename or "custom.ttf"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in {".ttf", ".otf"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Allowed formats: .ttf, .otf",
        )

    upload_dir = os.path.join(settings.UPLOAD_DIR, "fonts", str(client.id))
    os.makedirs(upload_dir, exist_ok=True)
    saved = f"{uuid.uuid4()}{ext}"
    filepath = os.path.join(upload_dir, saved)

    content = await file.read()
    if len(content) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Max font size is 8MB")
    if len(content) < 100:
        raise HTTPException(status_code=400, detail="Empty or invalid font file")

    async with aiofiles.open(filepath, "wb") as out:
        await out.write(content)

    stem = os.path.splitext(os.path.basename(filename))[0]
    family = (family_name or stem or "Custom").strip()[:80]

    result = await db.execute(
        select(Client)
        .options(selectinload(Client.branding))
        .where(Client.id == client.id)
    )
    row = result.scalar_one()
    branding = row.branding
    if not branding:
        branding = ClientBranding(client_id=client.id)
        db.add(branding)

    branding.subtitle_font_name = family
    branding.subtitle_font_path = filepath
    await db.commit()
    await db.refresh(branding)
    return {
        "message": "Custom font uploaded",
        "family": family,
        "path": filepath,
        "branding": {
            "subtitle_font_name": branding.subtitle_font_name,
            "subtitle_font_path": branding.subtitle_font_path,
        },
    }


@router.get("/assets/photos")
async def search_asset_photos(
    q: str,
    client: Client = Depends(get_current_client),
):
    """Search stock photos (needs PEXELS_API_KEY or UNSPLASH_ACCESS_KEY)."""
    from app.services.assets.library_service import AssetLibraryService
    lib = AssetLibraryService()
    photos = await lib.search_photos(q, per_page=12)
    return {
        "query": q,
        "ready": lib.status()["photos"]["ready"],
        "photos": photos,
    }


@router.get("/assets/preview")
async def preview_assets_for_topic(
    q: str,
    client: Client = Depends(get_current_client),
):
    """Show which theme/stickers/font would be auto-picked for a topic."""
    from app.services.assets.library_service import AssetLibraryService
    picked = await AssetLibraryService().auto_pick_for_video(q)
    return {
        "theme": picked["theme"],
        "font_id": picked["font_id"],
        "font_family": picked["font_family"],
        "stickers": [
            {"file": s.get("file"), "code": s.get("code"), "position": s.get("position")}
            for s in (picked.get("stickers") or [])
        ],
        "photo": picked.get("photo"),
    }
