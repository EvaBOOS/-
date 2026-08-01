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

if os.path.exists(frontend_admin_path):
    app.mount("/admin/static", StaticFiles(directory=os.path.join(frontend_admin_path, "static")), name="admin_static")

if os.path.exists(frontend_client_path):
    app.mount("/dashboard/static", StaticFiles(directory=os.path.join(frontend_client_path, "static")), name="client_static")


@app.get("/", response_class=HTMLResponse)
async def root():
    """Root endpoint with welcome page."""
    return """
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>VideoGen</title>
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
        <link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,700&family=Syne:wght@500;700;800&display=swap" rel="stylesheet">
        <style>
            :root {
                --ink: #12140f;
                --paper: #eef0e8;
                --field: #d9ddd2;
                --accent: #ff3b1f;
                --lime: #c8f000;
                --font-display: "Fraunces", Georgia, serif;
                --font-ui: "Syne", "Trebuchet MS", sans-serif;
            }
            * { box-sizing: border-box; margin: 0; padding: 0; }
            body {
                min-height: 100vh;
                font-family: var(--font-ui);
                color: var(--ink);
                background:
                    radial-gradient(1100px 560px at 8% 15%, rgba(200,240,0,.2), transparent 55%),
                    radial-gradient(900px 520px at 92% 85%, rgba(255,59,31,.16), transparent 50%),
                    linear-gradient(160deg, #c8cebb 0%, #e7ebe0 48%, #bcc3b2 100%);
                overflow-x: hidden;
            }
            .atmosphere { position: fixed; inset: 0; pointer-events: none; z-index: 0; }
            .fog {
                position: absolute; border-radius: 50%; filter: blur(70px); opacity: .7;
                animation: drift 16s ease-in-out infinite alternate;
            }
            .fog-a { width: 46vw; height: 46vw; left: -10%; top: 8%; background: rgba(255,255,255,.42); }
            .fog-b { width: 38vw; height: 38vw; right: -8%; bottom: 0; background: rgba(200,240,0,.2); animation-delay: -5s; }
            .trinket {
                position: absolute; border: 2px solid var(--ink); background: var(--paper);
                box-shadow: 7px 7px 0 var(--ink); animation: bob 6s ease-in-out infinite;
            }
            .t1 { width: 96px; height: 96px; left: 7%; top: 22%; transform: rotate(-9deg);
                  background: radial-gradient(circle at 50% 45%, var(--accent) 0 18%, transparent 19%), var(--paper); }
            .t2 { width: 70px; height: 70px; right: 10%; top: 26%; border-radius: 50%; background: var(--lime); }
            .t3 { width: 120px; height: 44px; left: 14%; bottom: 12%;
                  background: repeating-linear-gradient(-12deg, var(--ink) 0 6px, var(--paper) 6px 12px); }
            @keyframes drift { from { transform: translate(0,0) scale(1);} to { transform: translate(28px,-20px) scale(1.06);} }
            @keyframes bob { 0%,100% { transform: translateY(0) rotate(-9deg);} 50% { transform: translateY(-12px) rotate(-3deg);} }
            .t2 { animation-name: bob2; }
            @keyframes bob2 { 0%,100% { transform: translateY(0);} 50% { transform: translateY(-14px);} }

            .shell {
                position: relative; z-index: 1; min-height: 100vh;
                display: grid; place-items: center; padding: 40px 20px;
            }
            .hero {
                width: min(760px, 100%);
                padding: clamp(28px, 5vw, 48px);
                background: color-mix(in srgb, var(--paper) 84%, transparent);
                backdrop-filter: blur(18px);
                border: 2px solid var(--ink);
                box-shadow: 12px 12px 0 rgba(18,20,15,.88);
                animation: rise .75s cubic-bezier(.2,.8,.2,1) both;
            }
            @keyframes rise { from { opacity:0; transform: translateY(22px);} to { opacity:1; transform: none;} }
            .brand {
                font-family: var(--font-display);
                font-size: clamp(3rem, 9vw, 5rem);
                font-weight: 700; letter-spacing: -.045em; line-height: .92;
                margin-bottom: 18px;
            }
            .headline {
                font-family: var(--font-display);
                font-size: clamp(1.45rem, 3.4vw, 2.1rem);
                font-weight: 500; letter-spacing: -.02em; line-height: 1.15;
                margin-bottom: 12px; max-width: 18ch;
            }
            .lead {
                font-size: 1.05rem; line-height: 1.55; max-width: 42ch;
                margin-bottom: 28px; color: #2a2f27;
            }
            .actions { display: flex; flex-wrap: wrap; gap: 12px; }
            .btn {
                display: inline-flex; align-items: center; justify-content: center;
                padding: 13px 20px; border: 2px solid var(--ink); background: var(--paper);
                color: var(--ink); text-decoration: none; font-family: var(--font-ui);
                font-weight: 800; font-size: .95rem; transition: transform .15s, box-shadow .15s, background .15s;
            }
            .btn:hover { transform: translate(-2px,-2px); box-shadow: 4px 4px 0 var(--ink); }
            .btn-primary { background: var(--accent); color: #fff7f5; }
            .btn-primary:hover { background: #ff5238; }
            .btn-lime { background: var(--lime); }
            .meta {
                margin-top: 28px; padding-top: 16px; border-top: 1px solid rgba(18,20,15,.15);
                display: flex; flex-wrap: wrap; gap: 10px 18px;
                font-size: .78rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase;
                color: #2a2f27;
            }
            .chip {
                display: inline-flex; align-items: center; padding: 5px 9px;
                border: 1.5px solid var(--ink); background: var(--field);
            }
            @media (max-width: 720px) {
                .trinket { display: none; }
            }
        </style>
    </head>
    <body>
        <div class="atmosphere" aria-hidden="true">
            <div class="fog fog-a"></div>
            <div class="fog fog-b"></div>
            <span class="trinket t1"></span>
            <span class="trinket t2"></span>
            <span class="trinket t3"></span>
        </div>
        <main class="shell">
            <section class="hero">
                <p class="brand">VideoGen</p>
                <h1 class="headline">Снимай коротко. Говори громко.</h1>
                <p class="lead">B2B-платформа для роликов: сценарий, голос, аватар и монтаж в одном пайплайне.</p>
                <div class="actions">
                    <a class="btn btn-primary" href="/dashboard">Кабинет клиента</a>
                    <a class="btn btn-lime" href="/admin">Админка</a>
                </div>
                <div class="meta">
                    <span class="chip">Сценарий</span>
                    <span class="chip">Озвучка</span>
                    <span class="chip">Аватар</span>
                    <span class="chip">v1.0.0</span>
                </div>
            </section>
        </main>
    </body>
    </html>
    """


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
