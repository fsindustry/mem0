"""SharedVectorStore:共享记忆 pgvector 检索副本(唯一存储)。

迁移自参考库 shared_space/vector_store.py(Milvus→pgvector,去后缀)。
collection mem0_shared(去 _{pid}),与 summary/membox 同构(id/embedding/metadata)。
metadata:space_id, data, user_id/agent_id, source_fact_id, created_at/updated_at。
按 space_id 标量过滤检索。
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from psycopg import sql
from psycopg.types.json import Json

logger = logging.getLogger(__name__)


def shared_collection_name() -> str:
    """共享记忆 collection 名:mem0_shared(去 _{pid},每 product 独立库)。"""
    return "mem0_shared"


class SharedVectorStore:
    def __init__(self, pool, collection_name: str = "mem0_shared", dims: int = 1536) -> None:
        self.pool = pool
        self.collection = collection_name
        self.dims = dims
        self._create_col()

    def _create_col(self) -> None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cur.execute(
                    sql.SQL(
                        "CREATE TABLE IF NOT EXISTS {} (id VARCHAR(64) PRIMARY KEY, embedding vector({}), metadata JSONB)"
                    ).format(sql.Identifier(self.collection), sql.Literal(self.dims))
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} USING hnsw (embedding vector_l2_ops)").format(
                        sql.Identifier(f"{self.collection}_emb_idx"), sql.Identifier(self.collection)
                    )
                )
            conn.commit()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _build_filter(self, filters: Optional[Dict[str, Any]]) -> Tuple[sql.Composed, list]:
        if not filters:
            return sql.SQL(""), []
        clauses: list[sql.Composed] = []
        params: list = []
        for key, value in filters.items():
            clauses.append(sql.SQL("metadata->>%s = %s"))
            params.extend([key, str(value)])
        return sql.SQL(" WHERE ") + sql.SQL(" AND ").join(clauses), params

    def upsert(self, ids: List[str], vectors: List[List[float]], payloads: List[Dict[str, Any]]) -> None:
        if not ids:
            return
        rows = []
        for i, v, p in zip(ids, vectors, payloads):
            p = dict(p)
            p.setdefault("created_at", self._now_iso())
            p.setdefault("updated_at", self._now_iso())
            rows.append((i, v, Json(p)))
        stmt = sql.SQL(
            "INSERT INTO {} (id, embedding, metadata) VALUES (%s, %s::vector, %s) "
            "ON CONFLICT (id) DO UPDATE SET embedding = EXCLUDED.embedding, metadata = EXCLUDED.metadata"
        ).format(sql.Identifier(self.collection))
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(stmt, rows)
            conn.commit()

    def get(self, memory_id: str) -> Optional[Dict[str, Any]]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("SELECT id, metadata FROM {} WHERE id = %s").format(sql.Identifier(self.collection)),
                    (memory_id,),
                )
                row = cur.fetchone()
        return {"id": row[0], "payload": row[1]} if row else None

    def search(self, vector: List[float], space_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        where, params = self._build_filter({"space_id": space_id})
        q = sql.SQL("SELECT id, metadata, embedding <-> %s::vector AS dist FROM {} {} ORDER BY dist LIMIT %s").format(
            sql.Identifier(self.collection), where
        )
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(q, [vector] + params + [limit])
                rows = cur.fetchall()
        return [{"id": r[0], "payload": r[1], "score": float(r[2])} for r in rows]

    def list(self, space_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        q = sql.SQL("SELECT id, metadata FROM {} WHERE metadata->>'space_id' = %s ORDER BY (metadata->>'updated_at') DESC LIMIT %s").format(
            sql.Identifier(self.collection)
        )
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(q, [space_id, limit])
                rows = cur.fetchall()
        return [{"id": r[0], "payload": r[1]} for r in rows]

    def count(self, space_id: str) -> int:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("SELECT COUNT(*) FROM {} WHERE metadata->>'space_id' = %s").format(sql.Identifier(self.collection)),
                    (space_id,),
                )
                row = cur.fetchone()
        return row[0] if row else 0

    def delete(self, ids: List[str]) -> None:
        if not ids:
            return
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql.SQL("DELETE FROM {} WHERE id = ANY(%s)").format(sql.Identifier(self.collection)), (list(ids),))
            conn.commit()

    def delete_by_space(self, space_id: str) -> int:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("DELETE FROM {} WHERE metadata->>'space_id' = %s").format(sql.Identifier(self.collection)),
                    (space_id,),
                )
                n = cur.rowcount
            conn.commit()
        return n
