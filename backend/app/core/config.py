from pydantic_settings import BaseSettings
from typing import Optional
import os


class Settings(BaseSettings):
    PROJECT_NAME: str = "AI Video Generator"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    
    # Security
    SECRET_KEY: str = "your-secret-key-change-in-production-min-32-chars"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days
    
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/videogen"
    DATABASE_URL_SYNC: str = "postgresql://postgres:postgres@localhost:5432/videogen"
    
    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    
    # AI API Keys
    OPENAI_API_KEY: Optional[str] = None
    ELEVENLABS_API_KEY: Optional[str] = None
    HEYGEN_API_KEY: Optional[str] = None
    
    # File Storage
    MEDIA_ROOT: str = "/workspace/media"
    UPLOAD_DIR: str = "/workspace/media/uploads"
    GENERATED_DIR: str = "/workspace/media/generated"
    MAX_UPLOAD_SIZE: int = 10 * 1024 * 1024  # 10MB
    
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
