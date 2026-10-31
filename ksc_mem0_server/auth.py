"""轻量鉴权 + 多 product 路由 dependency。

校验 X-API-Key(应用库 api_keys 表 sha256 比对,常量时间)与 ADMIN_API_KEY;
解析 X-Product-Id,经 ProductRegistry 取/建 ProductRuntime;
按 api_keys.product_scope 限制 key 可访问的 product。

AUTH_DISABLED=true 时跳过鉴权(仅本地开发)。
"""

import hashlib
import hmac
import logging

from fastapi import Header, HTTPException, Request
from sqlalchemy import select

from .config import get_settings
from .db import SessionLocal
from .models import ApiKey
from .runtime import get_registry

logger = logging.getLogger(__name__)


def hash_key(key: str) -> str:
    """API key 的存储哈希(sha256);明文 key 仅在校验时存在内存。"""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _verify_admin(key: str | None) -> bool:
    s = get_settings()
    return bool(s.admin_api_key and key and hmac.compare_digest(key, s.admin_api_key))


def _verify_api_key(key: str | None) -> ApiKey | None:
    """查应用库校验 API key,返回记录(未找到/已撤销返回 None)。"""
    if not key:
        return None
    kh = hash_key(key)
    with SessionLocal() as session:
        stmt = select(ApiKey).where(ApiKey.key_hash == kh, ApiKey.revoked_at.is_(None))
        return session.scalars(stmt).first()


async def get_runtime(
    request: Request,
    x_product_id: str = Header(..., alias="X-Product-Id"),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
):
    """FastAPI 依赖:鉴权 + 按 product_id 路由,返回 ProductRuntime。"""
    s = get_settings()

    if not s.auth_disabled:
        if x_api_key is None:
            raise HTTPException(status_code=401, detail="Missing X-API-Key header")
        if not _verify_admin(x_api_key):
            rec = _verify_api_key(x_api_key)
            if rec is None:
                raise HTTPException(status_code=401, detail="Invalid or revoked API key")
            if rec.product_scope != "*" and rec.product_scope != x_product_id:
                raise HTTPException(status_code=403, detail="API key not allowed for this product")

    runtime = get_registry().get_or_create(x_product_id)
    request.state.product_runtime = runtime
    return runtime
