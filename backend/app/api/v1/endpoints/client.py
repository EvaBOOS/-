from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, UploadFile, File, Form
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from typing import Optional
import logging
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
logger = logging.getLogger(__name__)


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


@router.post("/deactivate")
async def deactivate_account(
    client: Client = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """Self-service account deactivation. Flips the same `is_active` flag
    an admin can already toggle via PATCH /admin/clients/{id} — locks out
    every route depending on get_current_client, but keeps the account and
    its data intact and reversible by an admin. Full data/file erasure
    ("right to be forgotten") is separate, larger scope, not this route."""
    client.is_active = False
    await db.commit()
    return {"message": "Аккаунт деактивирован"}


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
    1. LLM generates viral script
    2. TTS synthesizes voice
    3. Avatar video with lip sync
    4. FFmpeg adds subtitles and watermark
    """
    # Check credits
    if client.credits_remaining <= 0:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="No credits remaining. Please contact admin to add more credits."
        )
    
    # Create generation record
    from app.services.video.content_presets import normalize_genre
    from app.services.media_ingest import validate_public_http_url as _validate_url

    genre_norm = normalize_genre(generation_data.genre)
    product_url = (generation_data.product_url or "").strip()
    # This mode is 100% synthesized (LLM script + TTS voice + AI avatar) --
    # there's no "non-AI" variant of it, so the disclosure flag is always on,
    # unlike viral-edit/clips where it reflects the user's own choice.
    api_meta = {"genre": genre_norm, "ai_disclosure_requested": True}
    if product_url:
        try:
            api_meta["product_url"] = _validate_url(product_url)
        except MediaIngestError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    generation = VideoGeneration(
        client_id=client.id,
        mode=GenerationMode.AVATAR.value,
        original_text=generation_data.original_text,
        target_language=generation_data.target_language,
        status=GenerationStatus.PENDING,
        api_responses=api_meta,
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
    platform: str = Form("auto"),
    intensity: str = Form("full"),
    genre: str = Form("default"),
    hook_variants: int = Form(1),
    music_mode: str = Form("auto"),
    kinetic_subtitles: bool = Form(False),
    volumetric_hook: bool = Form(False),
    rights_confirmed: bool = Form(False),
    ai_disclosure_requested: bool = Form(False),
    client: Client = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload a talking-head / raw clip (or paste a public URL) and run the viral-edit pipeline:
    Whisper (optional) → optional voiceover/dub → format → zooms/B-roll/hook/music → karaoke → virality score.
    Silent clips (no speech) are supported; pass voiceover_text to add TTS later.
    Optional: platform (shorts/reels/tiktok), intensity (full/lite), genre, hook_variants (1–3).
    music_mode: "auto" (default — client's uploaded track if set, else mood-based
    auto-pick) or "off" (keep the source clip's own audio, no bed on top at all).
    """
    if client.credits_remaining <= 0:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="No credits remaining. Please contact admin to add more credits.",
        )
    if not rights_confirmed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Подтвердите, что у вас есть права на загружаемый материал",
        )

    from app.services.video.edit_styles import VALID_STYLES
    from app.services.video.content_presets import (
        normalize_genre,
        normalize_intensity,
        normalize_platform,
        resolve_format,
    )

    style_norm = (style or "dynamic").strip().lower()
    if style_norm not in VALID_STYLES:
        style_norm = "dynamic"

    format_norm = (format or "9:16").strip()
    if format_norm not in {"9:16", "1:1", "16:9"}:
        format_norm = "9:16"

    platform_norm = normalize_platform(platform)
    intensity_norm = normalize_intensity(intensity)
    genre_norm = normalize_genre(genre)
    format_norm = resolve_format(format_norm, platform_norm)
    hooks_n = max(1, min(int(hook_variants or 1), 3))

    dub_norm = (dub_language or "").strip().lower()
    if dub_norm in {"", "same", "none", "original"}:
        dub_norm = ""

    font_norm = _normalize_font_id(font_id)
    vo_norm = (voiceover_text or "").strip()[:4000]
    music_mode_norm = (music_mode or "auto").strip().lower()
    if music_mode_norm not in {"auto", "off"}:
        music_mode_norm = "auto"

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
            "platform": platform_norm,
            "intensity": intensity_norm,
            "genre": genre_norm,
            "hook_variants": hooks_n,
            "music_mode": music_mode_norm,
            "kinetic_subtitles": bool(kinetic_subtitles),
            "volumetric_hook": bool(volumetric_hook),
            "rights_confirmed": bool(rights_confirmed),
            "ai_disclosure_requested": bool(ai_disclosure_requested),
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
    platform: str = Form("auto"),
    kinetic_subtitles: bool = Form(False),
    volumetric_hook: bool = Form(False),
    rights_confirmed: bool = Form(False),
    ai_disclosure_requested: bool = Form(False),
    client: Client = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload a long video (or paste a public URL); AI finds highlight moments and exports Shorts clips.
    Optional platform preset: shorts / reels / tiktok (duration targets + brain tags).
    """
    if client.credits_remaining <= 0:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="No credits remaining. Please contact admin to add more credits.",
        )
    if not rights_confirmed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Подтвердите, что у вас есть права на загружаемый материал",
        )

    from app.services.video.content_presets import normalize_platform

    clips_n = max(1, min(int(max_clips or 5), settings.AI_CLIPS_MAX_COUNT))
    font_norm = _normalize_font_id(font_id)
    platform_norm = normalize_platform(platform)

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
            "platform": platform_norm,
            "kinetic_subtitles": bool(kinetic_subtitles),
            "volumetric_hook": bool(volumetric_hook),
            "rights_confirmed": bool(rights_confirmed),
            "ai_disclosure_requested": bool(ai_disclosure_requested),
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


@router.get("/generations/{generation_id}/hooks/{hook_index}/download")
async def download_hook_variant(
    generation_id: int,
    hook_index: int,
    client: Client = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """Download an A/B hook export from a viral-edit job (2-based index matching hook_exports)."""
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
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Hooks are not ready")

    paths = (generation.api_responses or {}).get("hook_exports_abs") or []
    exports = (generation.api_responses or {}).get("hook_exports") or []
    # Allow lookup by export.index or by 1-based position in list
    path = None
    for i, item in enumerate(exports):
        if int(item.get("index") or (i + 2)) == hook_index:
            if i < len(paths):
                path = paths[i]
            break
    if not path and 1 <= hook_index <= len(paths):
        path = paths[hook_index - 1]
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hook variant not found")

    return FileResponse(
        path,
        media_type="video/mp4",
        filename=f"hook_{generation_id}_{hook_index}.mp4",
    )


@router.get("/presets")
async def list_content_presets(
    client: Client = Depends(get_current_client),
):
    """Platform / intensity / genre presets for client UI."""
    from app.services.video.content_presets import list_presets_public
    from app.services.video.edit_styles import STYLE_PRESETS

    data = list_presets_public()
    data["styles"] = [
        {"id": k, "label": v.get("label") or k} for k, v in STYLE_PRESETS.items()
    ]
    return data


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
        logger.exception("Font file unavailable: font_id=%s", font_id)
        is_production = (settings.ENVIRONMENT or "").strip().lower() in {"production", "prod"}
        detail = "Font unavailable" if is_production else f"Font unavailable: {e}"
        raise HTTPException(status_code=500, detail=detail) from e
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


@router.get("/branding/handwriting-template")
async def get_handwriting_template(
    client: Client = Depends(get_current_client),
):
    """Printable PNG template — fill one character per cell, photograph it,
    then POST it to /branding/handwriting-font."""
    from app.services.fonts.handwriting_template import generate_template_image

    png_bytes = await generate_template_image()
    return Response(content=png_bytes, media_type="image/png")


@router.post("/branding/handwriting-font")
async def build_handwriting_font(
    file: UploadFile = File(...),
    client: Client = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """Build a private TTF from a photo of the filled-in handwriting
    template and set it as this client's subtitle font. The font is stored
    the same way as an uploaded font — private to this client, not exposed
    to anyone else."""
    from app.models.client import ClientBranding
    from app.services.fonts.handwriting_font_service import (
        HandwritingFontError,
        build_font_from_photo,
    )

    content = await file.read()
    if len(content) > settings.MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="Файл слишком большой")

    try:
        font_bytes = build_font_from_photo(content)
    except HandwritingFontError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    upload_dir = os.path.join(settings.UPLOAD_DIR, "fonts", str(client.id))
    os.makedirs(upload_dir, exist_ok=True)
    filepath = os.path.join(upload_dir, f"{uuid.uuid4()}.ttf")

    async with aiofiles.open(filepath, "wb") as out:
        await out.write(font_bytes)

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

    branding.subtitle_font_name = "Мой почерк"
    branding.subtitle_font_path = filepath
    await db.commit()
    await db.refresh(branding)
    return {
        "message": "Шрифт из почерка собран",
        "family": branding.subtitle_font_name,
        "path": filepath,
        "branding": {
            "subtitle_font_name": branding.subtitle_font_name,
            "subtitle_font_path": branding.subtitle_font_path,
        },
    }


@router.post("/branding/music")
async def upload_client_music(
    file: UploadFile = File(...),
    track_name: str = Form(""),
    client: Client = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload a custom background-music track for viral-edit generations.
    When set, this replaces the automatic mood-based track pick entirely —
    the client is responsible for having the rights to whatever they upload,
    same as with any other media they submit to the platform.
    """
    from app.models.client import ClientBranding
    from app.services.video.ffmpeg_service import FFmpegService

    filename = file.filename or "custom.mp3"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in {".mp3", ".wav", ".m4a", ".aac", ".ogg"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Allowed formats: mp3, wav, m4a, aac, ogg",
        )

    upload_dir = os.path.join(settings.UPLOAD_DIR, "music", str(client.id))
    os.makedirs(upload_dir, exist_ok=True)
    saved = f"{uuid.uuid4()}{ext}"
    filepath = os.path.join(upload_dir, saved)

    content = await file.read()
    if len(content) > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Max music file size is 15MB")
    if len(content) < 1000:
        raise HTTPException(status_code=400, detail="Empty or invalid audio file")

    async with aiofiles.open(filepath, "wb") as out:
        await out.write(content)

    # Reject files that aren't actually decodable audio (wrong extension,
    # truncated upload, etc.) before they can break a later generation.
    duration = FFmpegService().get_video_duration(filepath)
    if duration <= 0:
        try:
            os.remove(filepath)
        except OSError:
            pass
        raise HTTPException(status_code=400, detail="File is not valid audio")

    stem = os.path.splitext(os.path.basename(filename))[0]
    name = (track_name or stem or "Custom track").strip()[:80]

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

    branding.custom_music_name = name
    branding.custom_music_path = filepath
    await db.commit()
    await db.refresh(branding)
    return {
        "message": "Custom music uploaded",
        "name": name,
        "duration_sec": round(duration, 1),
        "branding": {
            "custom_music_name": branding.custom_music_name,
            "custom_music_path": branding.custom_music_path,
        },
    }


@router.delete("/branding/music")
async def delete_client_music(
    client: Client = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """Remove the custom track — generations go back to automatic mood-based music."""
    result = await db.execute(
        select(Client)
        .options(selectinload(Client.branding))
        .where(Client.id == client.id)
    )
    row = result.scalar_one()
    branding = row.branding
    if not branding or not branding.custom_music_path:
        return {"message": "No custom music set"}

    old_path = branding.custom_music_path
    branding.custom_music_path = None
    branding.custom_music_name = None
    await db.commit()
    try:
        if old_path and os.path.isfile(old_path):
            os.remove(old_path)
    except OSError:
        pass
    return {"message": "Custom music removed"}


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
