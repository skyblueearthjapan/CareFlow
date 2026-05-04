"""v1 API router aggregation."""

from fastapi import APIRouter

from app.api.v1 import admin, auth, health

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(admin.router)

__all__ = ["api_router"]
