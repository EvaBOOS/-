from fastapi import APIRouter
from app.api.v1.endpoints import auth, admin, client, trends, public, payments, moderation_admin

api_router = APIRouter()

api_router.include_router(
    auth.router,
    prefix="/auth",
    tags=["Authentication"]
)

api_router.include_router(
    public.router,
    prefix="/public",
    tags=["Public"]
)

api_router.include_router(
    admin.router,
    prefix="/admin",
    tags=["Admin Panel"]
)

api_router.include_router(
    moderation_admin.router,
    prefix="/admin",
    tags=["Moderation"]
)

api_router.include_router(
    client.router,
    prefix="/client",
    tags=["Client Dashboard"]
)

api_router.include_router(
    trends.router,
    prefix="/client",
    tags=["Trend Radar"]
)

api_router.include_router(
    payments.router,
    prefix="/client",
    tags=["Payments"]
)
