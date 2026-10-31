"""ProductRegistry:product_id -> ProductRuntime 的线程安全缓存 + 按需建库。

单实例多 product 路由的核心:每个请求带 product_id,middleware 经 get_or_create 取得
对应 ProductRuntime(首次访问时建库 + 登记 + 实例化并缓存)。

跨 uvicorn worker 注意:多进程无共享 lock,CREATE DATABASE 用"先查 pg_database 再建
+ 容忍 DuplicateDatabase"保证幂等。
"""

import logging
import threading

from ..config import get_settings
from ..db import SessionLocal
from ..models import Product
from .product import ProductRuntime
from .schema import create_product_database

logger = logging.getLogger(__name__)


class ProductRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._runtimes: dict[str, ProductRuntime] = {}

    def get_or_create(self, product_id: str) -> ProductRuntime:
        """取或建 ProductRuntime(线程安全,double-check 缓存)。"""
        rt = self._runtimes.get(product_id)
        if rt is not None:
            return rt
        with self._lock:
            rt = self._runtimes.get(product_id)
            if rt is not None:
                return rt
            overrides = self._ensure_registered(product_id)
            rt = ProductRuntime(product_id, config_overrides=overrides)
            self._runtimes[product_id] = rt
            return rt

    def _ensure_registered(self, product_id: str) -> dict | None:
        """查应用库 products;不存在则建库 + 登记,返回 config_overrides。"""
        s = get_settings()
        db_name = s.product_db_name(product_id)
        with SessionLocal() as session:
            p = session.get(Product, product_id)
            if p is None:
                create_product_database(product_id)
                p = Product(product_id=product_id, db_name=db_name, status="active")
                session.add(p)
                session.commit()
                session.refresh(p)
                logger.info("Product registered: %s -> db %s", product_id, db_name)
            return p.config_overrides

    def get(self, product_id: str) -> ProductRuntime | None:
        with self._lock:
            return self._runtimes.get(product_id)

    def close_all(self) -> None:
        with self._lock:
            for rt in list(self._runtimes.values()):
                rt.close()
            self._runtimes.clear()


_registry: ProductRegistry | None = None


def get_registry() -> ProductRegistry:
    global _registry
    if _registry is None:
        _registry = ProductRegistry()
    return _registry


def reset_registry() -> None:
    """测试用:关闭并清空全局 registry。"""
    global _registry
    if _registry is not None:
        _registry.close_all()
    _registry = None
