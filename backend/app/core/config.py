from pydantic_settings import BaseSettings
from typing import Optional
import os


class Settings(BaseSettings):
    PROJECT_NAME: str = "AI Video Generator"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    # development | production — production refuses insecure default SECRET_KEY
    ENVIRONMENT: str = "development"
    
    # Security
    SECRET_KEY: str = "your-secret-key-change-in-production-min-32-chars"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days
    # Comma-separated origins allowed to make credentialed cross-origin API calls
    # (e.g. "https://app.example.com,https://admin.example.com"). Empty by default:
    # the bundled admin/client panels are served by this same app and only ever
    # call relative /api/... URLs, so no cross-origin access is needed out of the box.
    # NEVER set this to "*" together with credentials — browsers require an explicit
    # origin for credentialed requests, so FastAPI/Starlette would reflect back
    # whatever Origin the caller sends, i.e. effectively allow any website.
    CORS_ORIGINS: str = ""
    
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/videogen"
    DATABASE_URL_SYNC: str = "postgresql://postgres:postgres@localhost:5432/videogen"
    
    # Redis / Celery
    REDIS_URL: str = "redis://localhost:6379/0"
    # None = auto (Celery when not sqlite + Redis up); True/False force
    USE_CELERY: Optional[bool] = None
    
    # AI API Keys
    # Script LLM: AITUNNEL (preferred, ₽) → fallback OpenAI
    AITUNNEL_API_KEY: Optional[str] = None
    AITUNNEL_BASE_URL: str = "https://api.aitunnel.ru/v1/"
    AITUNNEL_MODEL: str = "gemini-2.5-flash"
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4o-mini"
    ELEVENLABS_API_KEY: Optional[str] = None
    # Voice fallback when ElevenLabs unavailable (blocked regions / no key)
    EDGE_TTS_VOICE: str = "ru-RU-SvetlanaNeural"
    HEYGEN_API_KEY: Optional[str] = None
    # HeyGen TTS voice (used only if audio upload fails). Must be a HeyGen voice_id, not Edge/Azure names.
    HEYGEN_VOICE_ID: str = "5f99970adadb42398bf1aeb963a3888b"  # Dmitry (Russian)
    # Default public avatar look (josh_lite3_* is retired)
    HEYGEN_AVATAR_ID: str = "Abigail_expressive_2024112501"
    # Solid backdrop behind avatar (studio looks often ship on white)
    HEYGEN_BACKGROUND_COLOR: str = "#0B1220"
    # Local sticker pack (Typiq PNGs / Twemoji). Relative to MEDIA_ROOT or absolute.
    ASSETS_DIR: str = "./media/assets"
    STICKERS_ENABLED: bool = True
    # Auto-pick cool Google Fonts for subtitles when client has no custom font
    AUTO_FONT_ENABLED: bool = True
    # Free stock photos (optional — stickers/fonts work without these)
    PEXELS_API_KEY: Optional[str] = None
    UNSPLASH_ACCESS_KEY: Optional[str] = None
    # Free-tier CC-licensed background music (optional). Get a client_id at
    # https://developer.jamendo.com — results are filtered to CC-BY/CC0 only
    # (no -NC/-ND), never NC/ND tracks. Without a key, the mood-track/pad
    # fallback chain in MusicLibraryService is used as before.
    JAMENDO_CLIENT_ID: Optional[str] = None
    # Free-tier SFX (whoosh on zooms, pop on hooks) via Freesound.org — get a
    # token at https://freesound.org/apiv2/apply/. Results are filtered to
    # CC0/CC-BY licenses only. Without a key, SFX stings are silently skipped.
    FREESOUND_API_KEY: Optional[str] = None
    SFX_ENABLED: bool = True
    # Whisper model via AITUNNEL / OpenAI-compatible STT
    WHISPER_MODEL: str = "whisper-1"
    
    # File Storage
    MEDIA_ROOT: str = "/workspace/media"
    UPLOAD_DIR: str = "/workspace/media/uploads"
    GENERATED_DIR: str = "/workspace/media/generated"
    MAX_UPLOAD_SIZE: int = 10 * 1024 * 1024  # 10MB
    MAX_VIRAL_UPLOAD_SIZE: int = 80 * 1024 * 1024  # 80MB source videos
    MAX_CLIPS_UPLOAD_SIZE: int = 400 * 1024 * 1024  # 400MB long-form for AI clips
    AI_CLIPS_MAX_COUNT: int = 5
    # Import video by public URL (YouTube / Reels / TikTok …)
    LINK_INGEST_ENABLED: bool = True
    # Optional self-hosted Cobalt (https://github.com/imputnet/cobalt); yt-dlp used otherwise
    COBALT_API_URL: Optional[str] = None
    COBALT_API_KEY: Optional[str] = None
    # yt-dlp auth for YouTube bot-check ("Sign in to confirm you're not a bot")
    # Example: YTDLP_COOKIES_FROM_BROWSER=edge  or  chrome  or  chrome:Default
    YTDLP_COOKIES_FROM_BROWSER: Optional[str] = None
    # Or path to Netscape cookies.txt exported from browser
    YTDLP_COOKIES_FILE: Optional[str] = None
    # Trend radar
    YOUTUBE_API_KEY: Optional[str] = None
    TRENDS_CACHE_HOURS: int = 6
    TRENDS_DEFAULT_REGION: str = "RU"
    TRENDS_ANALYZE_COST_CREDITS: int = 1
    TIKTOK_TRENDS_ENABLED: bool = True

    # VideoGen Brain — structured rules injected into LLM prompts
    BRAIN_ENABLED: bool = True
    BRAIN_MAX_RULES: int = 12

    # Video Settings
    VIDEO_WIDTH: int = 1080
    VIDEO_HEIGHT: int = 1920
    VIDEO_FPS: int = 30
    VIDEO_BITRATE: str = "5000k"
    
    # Default Admin
    FIRST_ADMIN_EMAIL: str = "admin@example.com"
    FIRST_ADMIN_PASSWORD: str = "changeme123"
    
    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()

_INSECURE_SECRET_KEYS = {
    "your-secret-key-change-in-production-min-32-chars",
    "dev-local-secret-key-change-in-prod-32c",
    "change-me",
    "secret",
}


def _validate_secret_key() -> None:
    key = (settings.SECRET_KEY or "").strip()
    env = (settings.ENVIRONMENT or "development").strip().lower()
    weak = (not key) or (key in _INSECURE_SECRET_KEYS) or (len(key) < 32)
    if env in {"production", "prod"} and weak:
        raise RuntimeError(
            "Insecure SECRET_KEY in production. Set ENVIRONMENT=production only with a "
            "unique SECRET_KEY of at least 32 random characters."
        )
    if weak:
        import logging
        logging.getLogger(__name__).warning(
            "Using a weak/default SECRET_KEY — fine for local SQLite, unsafe for any public deploy."
        )


_INSECURE_ADMIN_PASSWORDS = {"changeme123", "admin", "password", "12345678"}


def _validate_admin_password() -> None:
    pwd = (settings.FIRST_ADMIN_PASSWORD or "").strip()
    env = (settings.ENVIRONMENT or "development").strip().lower()
    weak = (not pwd) or (pwd.lower() in _INSECURE_ADMIN_PASSWORDS) or (len(pwd) < 8)
    if env in {"production", "prod"} and weak:
        raise RuntimeError(
            "Insecure FIRST_ADMIN_PASSWORD in production. Set ENVIRONMENT=production only "
            "with a unique admin password (min 8 chars, not a known default). Note this only "
            "affects the admin row created on first boot — rotate an existing admin's password "
            "via POST /api/v1/auth/change-password."
        )
    if weak:
        import logging
        logging.getLogger(__name__).warning(
            "Using a weak/default FIRST_ADMIN_PASSWORD — fine for local dev, unsafe for any public deploy."
        )


_validate_secret_key()
_validate_admin_password()
