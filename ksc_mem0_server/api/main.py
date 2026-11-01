"""路由聚合:把各业务子路由挂到 api_router 上。"""

from fastapi import APIRouter

from .extraction import router as extraction_router
from .memories import router as memories_router
from .products import router as products_router
from .profile import router as profile_router
from .strategies import router as strategies_router
from .summary import router as summary_router

api_router = APIRouter()
api_router.include_router(memories_router)
api_router.include_router(products_router)
api_router.include_router(strategies_router)
api_router.include_router(profile_router)
api_router.include_router(summary_router)
api_router.include_router(extraction_router)

__all__ = ["api_router"]
