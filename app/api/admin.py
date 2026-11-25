from fastapi import APIRouter

from app.core.auth import AdminAuth

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[AdminAuth])