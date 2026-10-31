"""FastAPI 应用工厂 + lifespan。

lifespan:启动时初始化应用库(建库 + 建表);关闭时释放所有 ProductRuntime(关连接池)。
后台 worker(失败补偿 scanner / 策略 watcher)在 P1+ 的 lifespan 中追加启动。
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api.main import api_router
from .config import get_settings
from .db import init_app_db
from .runtime import get_registry

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_app_db()
    logger.info("App DB initialized")
    yield
    try:
        get_registry().close_all()
    except Exception:
        logger.warning("close_all failed during shutdown", exc_info=True)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="ksc-mem0 PG memory service",
        version="0.1.0",
        description="全 PostgreSQL、按 product_id 分库的多租户记忆服务(基于 mem0 SDK)。",
        lifespan=lifespan,
    )
    app.include_router(api_router)

    @app.get("/health", tags=["Health"])
    def health():
        return {"status": "ok", "auth_disabled": settings.auth_disabled}

    @app.get("/ready", tags=["Health"])
    def ready():
        return {"ready": True}

    return app
