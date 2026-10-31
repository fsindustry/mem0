"""pgvector 实例工厂:为 per-product 库的各 collection 创建 PGVector,共享同一连接池。

PGVector.__init__ 支持传入 connection_pool(优先级最高,跳过自建 pool),
因此一个 product 库内的事实/画像/摘要/membox/memscene/shared 6 张向量表
可由 6 个 PGVector 实例共享同一个 psycopg3 ConnectionPool,避免连接数膨胀。
(见 mem0/vector_stores/pgvector.py:166)
"""

from mem0.vector_stores.pgvector import PGVector

from ..config import get_settings


def create_pgvector(
    db_name: str,
    collection_name: str,
    embedding_model_dims: int,
    pool,
    *,
    diskann: bool = False,
    hnsw: bool = True,
) -> PGVector:
    """创建共享连接池的 PGVector 实例。

    Args:
        db_name: 该 product 库名(有 pool 时不用于连接,但 PGVector 构造需要参数)。
        collection_name: 库内向量表名(mem0 / mem0_profile / ...)。
        embedding_model_dims: 向量维度。
        pool: 该 product 库的共享 psycopg3 ConnectionPool。
        diskann/hnsw: 索引策略(P0 默认 HNSW)。
    """
    s = get_settings()
    return PGVector(
        dbname=db_name,
        collection_name=collection_name,
        embedding_model_dims=embedding_model_dims,
        user=s.pg_user,
        password=s.pg_password,
        host=s.pg_host,
        port=s.pg_port,
        diskann=diskann,
        hnsw=hnsw,
        connection_pool=pool,
    )
