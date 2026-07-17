from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from contextlib import asynccontextmanager
import os

from app.core.config import settings
from app.api.v1.router import api_router
from app.db.session import engine, Base


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    await create_default_admin()
    
    yield
    
    await engine.dispose()


async def create_default_admin():
    """Create default admin user if not exists."""
    from app.db.session import AsyncSessionLocal
    from app.models.user import User, UserRole
    from app.core.security import get_password_hash
    from sqlalchemy import select
    
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User).where(User.email == settings.FIRST_ADMIN_EMAIL)
        )
        existing = result.scalar_one_or_none()
        
        if not existing:
            admin = User(
                email=settings.FIRST_ADMIN_EMAIL,
                hashed_password=get_password_hash(settings.FIRST_ADMIN_PASSWORD),
                full_name="Administrator",
                role=UserRole.ADMIN,
                is_active=True
            )
            db.add(admin)
            await db.commit()
            print(f"Created default admin: {settings.FIRST_ADMIN_EMAIL}")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="""
    ## AI Video Generator SaaS Platform
    
    B2B platform for automated short-form video generation using AI.
    
    ### Features:
    - **Admin Panel**: Manage clients, subscriptions, and branding
    - **Client Dashboard**: Submit scripts and download generated videos
    - **AI Pipeline**: GPT-4o → ElevenLabs → HeyGen → FFmpeg
    
    ### Authentication:
    All endpoints (except login) require Bearer token authentication.
    """,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.API_V1_STR)

os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
os.makedirs(settings.GENERATED_DIR, exist_ok=True)

if os.path.exists(settings.MEDIA_ROOT):
    app.mount("/media", StaticFiles(directory=settings.MEDIA_ROOT), name="media")

# Mount frontend static files
frontend_admin_path = "/workspace/frontend/admin"
frontend_client_path = "/workspace/frontend/client"

if os.path.exists(frontend_admin_path):
    app.mount("/admin/static", StaticFiles(directory=f"{frontend_admin_path}/static"), name="admin_static")

if os.path.exists(frontend_client_path):
    app.mount("/dashboard/static", StaticFiles(directory=f"{frontend_client_path}/static"), name="client_static")


@app.get("/", response_class=HTMLResponse)
async def root():
    """Root endpoint with welcome page."""
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>AI Video Generator</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
            }
            .container {
                background: white;
                border-radius: 20px;
                padding: 40px;
                box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25);
                max-width: 500px;
                text-align: center;
            }
            h1 {
                color: #1a1a2e;
                margin-bottom: 10px;
                font-size: 2rem;
            }
            .subtitle {
                color: #666;
                margin-bottom: 30px;
            }
            .links {
                display: flex;
                gap: 15px;
                justify-content: center;
                flex-wrap: wrap;
            }
            a {
                display: inline-block;
                padding: 12px 24px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                text-decoration: none;
                border-radius: 8px;
                font-weight: 500;
                transition: transform 0.2s, box-shadow 0.2s;
            }
            a:hover {
                transform: translateY(-2px);
                box-shadow: 0 10px 20px -10px rgba(102, 126, 234, 0.5);
            }
            .version {
                margin-top: 30px;
                color: #999;
                font-size: 0.9rem;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🎬 AI Video Generator</h1>
            <p class="subtitle">B2B SaaS Platform for Automated Video Creation</p>
            <div class="links">
                <a href="/docs">API Documentation</a>
                <a href="/admin">Admin Panel</a>
                <a href="/dashboard">Client Dashboard</a>
            </div>
            <p class="version">Version 1.0.0</p>
        </div>
    </body>
    </html>
    """


@app.get("/admin", response_class=HTMLResponse)
async def admin_panel():
    """Serve admin panel."""
    admin_path = "/workspace/frontend/admin/index.html"
    if os.path.exists(admin_path):
        with open(admin_path, "r", encoding="utf-8") as f:
            return f.read()
    return HTMLResponse(content="<h1>Admin panel not found</h1>", status_code=404)


@app.get("/dashboard", response_class=HTMLResponse)
async def client_dashboard():
    """Serve client dashboard."""
    client_path = "/workspace/frontend/client/index.html"
    if os.path.exists(client_path):
        with open(client_path, "r", encoding="utf-8") as f:
            return f.read()
    return HTMLResponse(content="<h1>Dashboard not found</h1>", status_code=404)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "version": settings.VERSION,
        "service": settings.PROJECT_NAME
    }


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler."""
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "message": str(exc) if settings.SECRET_KEY == "your-secret-key-change-in-production-min-32-chars" else "An error occurred"
        }
    )
