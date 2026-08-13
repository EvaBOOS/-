from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from contextlib import asynccontextmanager
import os

from app.core.config import settings
from app.api.v1.router import api_router
from app.db.session import engine, Base
from app.db.migrate import ensure_schema_patches
import app.models  # noqa: F401 — register all ORM tables for create_all


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await ensure_schema_patches(engine, settings.DATABASE_URL)

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
    - **AI Pipeline**: сценарий → озвучка → аватар → FFmpeg
    
    ### Authentication:
    All endpoints (except login) require Bearer token authentication.
    """,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

_cors_origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
if "*" in _cors_origins:
    # A comment warning against CORS_ORIGINS=* isn't enforcement — an operator
    # can still set it. Strip it in code too: allow_credentials=True + "*" is
    # exactly the combination Starlette turns into "reflect any Origin",
    # i.e. the original vulnerability this setting exists to prevent.
    import logging
    logging.getLogger(__name__).warning(
        "CORS_ORIGINS contains \"*\" — ignoring it. Wildcard origins cannot be "
        "combined with credentialed requests; list explicit origins instead."
    )
    _cors_origins = [o for o in _cors_origins if o != "*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    # Only allow credentialed (Authorization-header) cross-origin requests for
    # explicitly configured origins — never combine allow_credentials with "*".
    allow_credentials=bool(_cors_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.API_V1_STR)

os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
os.makedirs(settings.GENERATED_DIR, exist_ok=True)
# Cookies live under backend/secrets (never mounted as static)
os.makedirs(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "secrets")), exist_ok=True)

# Public static: ONLY branding watermarks (UI preview). Do NOT mount full MEDIA_ROOT —
# that exposed youtube cookies + generated MP4s without auth.
_wm_dir = os.path.join(settings.MEDIA_ROOT, "uploads", "watermarks")
os.makedirs(_wm_dir, exist_ok=True)
app.mount(
    "/media/uploads/watermarks",
    StaticFiles(directory=_wm_dir),
    name="watermarks",
)

# Mount frontend static files (Docker: /app/frontend, local: <repo>/frontend)
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_FRONTEND_CANDIDATES = [
    os.path.join(_PROJECT_ROOT, "frontend"),
    "/app/frontend",
    "/workspace/frontend",
]
_frontend_root = next((p for p in _FRONTEND_CANDIDATES if os.path.exists(p)), _FRONTEND_CANDIDATES[0])
frontend_admin_path = os.path.join(_frontend_root, "admin")
frontend_client_path = os.path.join(_frontend_root, "client")
frontend_apply_path = os.path.join(_frontend_root, "apply")
frontend_landing_path = os.path.join(_frontend_root, "landing")
frontend_legal_path = os.path.join(_frontend_root, "legal")

if os.path.exists(frontend_admin_path):
    app.mount("/admin/static", StaticFiles(directory=os.path.join(frontend_admin_path, "static")), name="admin_static")

if os.path.exists(frontend_client_path):
    app.mount("/dashboard/static", StaticFiles(directory=os.path.join(frontend_client_path, "static")), name="client_static")

if os.path.exists(frontend_apply_path):
    app.mount("/apply/static", StaticFiles(directory=os.path.join(frontend_apply_path, "static")), name="apply_static")

if os.path.exists(frontend_landing_path):
    app.mount("/landing/static", StaticFiles(directory=os.path.join(frontend_landing_path, "static")), name="landing_static")

if os.path.exists(frontend_legal_path):
    app.mount("/legal/static", StaticFiles(directory=os.path.join(frontend_legal_path, "static")), name="legal_static")

# Draft compliance-doc pages (see frontend/legal/) — placeholders, not final
# legal text; each doc_id maps to a static file served through the explicit
# route below, same convention as /admin, /dashboard, /apply.
_LEGAL_DOCS = {
    "terms": "terms.html",
    "privacy": "privacy.html",
    "cookies": "cookies.html",
    "refunds": "refunds.html",
    "ai-disclosure": "ai-disclosure.html",
}


@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the public landing page."""
    landing_path = os.path.join(frontend_landing_path, "index.html")
    if os.path.exists(landing_path):
        with open(landing_path, "r", encoding="utf-8") as f:
            return f.read()
    return HTMLResponse(content="<h1>LoudCut</h1>", status_code=200)


@app.get("/admin", response_class=HTMLResponse)
async def admin_panel():
    """Serve admin panel."""
    admin_path = os.path.join(frontend_admin_path, "index.html")
    if os.path.exists(admin_path):
        with open(admin_path, "r", encoding="utf-8") as f:
            return f.read()
    return HTMLResponse(content="<h1>Admin panel not found</h1>", status_code=404)


@app.get("/dashboard", response_class=HTMLResponse)
async def client_dashboard():
    """Serve client dashboard."""
    client_path = os.path.join(frontend_client_path, "index.html")
    if os.path.exists(client_path):
        with open(client_path, "r", encoding="utf-8") as f:
            return f.read()
    return HTMLResponse(content="<h1>Dashboard not found</h1>", status_code=404)


@app.get("/apply", response_class=HTMLResponse)
async def apply_page():
    """Serve the public campaign/blogger access-request form."""
    apply_path = os.path.join(frontend_apply_path, "index.html")
    if os.path.exists(apply_path):
        with open(apply_path, "r", encoding="utf-8") as f:
            return f.read()
    return HTMLResponse(content="<h1>Apply page not found</h1>", status_code=404)


@app.get("/legal", response_class=HTMLResponse)
async def legal_hub():
    """Serve the compliance-docs hub page (draft placeholders)."""
    hub_path = os.path.join(frontend_legal_path, "index.html")
    if os.path.exists(hub_path):
        with open(hub_path, "r", encoding="utf-8") as f:
            return f.read()
    return HTMLResponse(content="<h1>Legal hub not found</h1>", status_code=404)


@app.get("/legal/{doc_id}", response_class=HTMLResponse)
async def legal_doc(doc_id: str):
    """Serve one draft compliance document by id (see _LEGAL_DOCS)."""
    filename = _LEGAL_DOCS.get(doc_id)
    if not filename:
        return HTMLResponse(content="<h1>Document not found</h1>", status_code=404)
    doc_path = os.path.join(frontend_legal_path, "docs", filename)
    if os.path.exists(doc_path):
        with open(doc_path, "r", encoding="utf-8") as f:
            return f.read()
    return HTMLResponse(content="<h1>Document not found</h1>", status_code=404)


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
    is_production = (settings.ENVIRONMENT or "").strip().lower() in {"production", "prod"}
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "message": "An error occurred" if is_production else str(exc)
        }
    )
