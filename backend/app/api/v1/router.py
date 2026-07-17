from fastapi import APIRouter
from app.api.v1.endpoints import auth, admin, client

api_router = APIRouter()

api_router.include_router(
    auth.router,
    prefix="/auth",
    tags=["Authentication"]
)

api_router.include_router(
    admin.router,
    prefix="/admin",
    tags=["Admin Panel"]
)

api_router.include_router(
    client.router,
    prefix="/client",
    tags=["Client Dashboard"]
)
