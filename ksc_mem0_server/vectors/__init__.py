"""向量存储封装:per-product 库内多张 pgvector 表共享连接池。"""

from .pg_vector_set import create_pgvector

__all__ = ["create_pgvector"]
