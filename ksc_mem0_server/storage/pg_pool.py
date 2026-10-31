"""PostgreSQL 连接池工厂(psycopg3)。

每个 product 库复用一个 ConnectionPool;pgvector 的 6 张向量表通过
`PGVector(connection_pool=...)` 共享同一 pool,避免连接数膨胀。
"""

import logging
import re

from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)


def _mask_conninfo(conninfo: str) -> str:
    """脱敏 conninfo 中的密码,用于日志。"""
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", conninfo)


def create_pool(conninfo: str, *, min_size: int = 1, max_size: int = 5, open_: bool = True) -> ConnectionPool:
    """创建 psycopg3 ConnectionPool。

    Args:
        conninfo: psycopg3 连接串,如 postgresql://user:pass@host:port/dbname
        min_size/max_size: 连接池大小;per-product 库默认较小,避免 N product 膨胀。
        open_: True 时立即打开连接;测试可设 False 延迟。
    """
    pool = ConnectionPool(conninfo=conninfo, min_size=min_size, max_size=max_size, open=open_)
    logger.info("Created PG pool: %s (min=%s max=%s)", _mask_conninfo(conninfo), min_size, max_size)
    return pool
