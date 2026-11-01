"""FastAPI 应用工厂 + lifespan。

lifespan:启动时初始化应用库;启动失败补偿 scanner;关闭时释放资源。
"""

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api.main import api_router
from .config import get_settings
from .db import init_app_db
from .extraction import FailureCompensationScanner
from .runtime import get_registry

logger = logging.getLogger(__name__)

_stop_event = threading.Event()
_scanner: FailureCompensationScanner | None = None


def _rerun(runtime, failure: dict) -> bool:
    """失败补偿 rerun 回调:按 strategy_type 路由到对应子系统的重跑。

    P2 提供基础 rerun:profile/summary 策略触发对应 runtime.review;fact 触发 facts.add
    重跑(此处简化为按 strategy_type 路由 review)。失败记录的 episode_id 对应一次输入,
    重跑把 inputs 重新喂入对应 runtime。具体 inputs 还原在 P5 完善,此处先按 strategy_type 路由。
    """
    stype = failure.get("strategy_type", "fact")
    user_id = failure.get("user_id") or ""
    try:
        if stype == "profile":
            runtime.profile_runtime.review(user_id, failure.get("strategy_id", "default"))
        elif stype == "summary":
            runtime.summary_runtime.review(user_id, failure.get("run_id", "default"))
        else:
            # fact:无独立 review,记录为成功(避免无限重试),P5 完善 fact 重跑
            return True
        return True
    except Exception:
        logger.exception("rerun failed for failure %s", failure.get("id"))
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_app_db()
    logger.info("App DB initialized")
    global _scanner
    _scanner = FailureCompensationScanner(_stop_event, get_registry(), _rerun, interval=30.0)
    _scanner.start()
    logger.info("Compensation scanner started")
    yield
    _stop_event.set()
    try:
        if _scanner is not None:
            _scanner.join(timeout=5)
        get_registry().close_all()
    except Exception:
        logger.warning("shutdown cleanup failed", exc_info=True)


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
