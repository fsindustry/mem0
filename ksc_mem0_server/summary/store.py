"""SummaryVectorStore:摘要向量存储(pgvector,自建 mem0_summary 表)。

迁移自参考库 summary/vector_store.py(Milvus→pgvector),去后缀。
条目化存储:按 (user_id, run_id, strategy_name) 维护多条(每章节每点一条)。
metadata:user_id/run_id/agent_id, data(content), section, strategy_name/id, version, snapshot_id, created_at/updated_at。
查询要求至少指定 user_id / run_id 之一(防全表扫描)。
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from psycopg import sql
from psycopg.types.json import Json

logger = logging.getLogger(__name__)

SUMMARY_SCOPE_KEYS = ("user_id", "agent_id", "run_id")


class SummaryVectorStore:
    def __init__(self, pool, collection_name: str = "mem0_summary", dims: int = 1536) -> None:
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

    def _require_scope(self, filters: Optional[Dict[str, Any]]) -> None:
        if not filters or not any(filters.get(k) and filters.get(k) != "*" for k in SUMMARY_SCOPE_KEYS):
            raise ValueError("summary query requires at least one of user_id/agent_id/run_id")

    def _build_filter(self, filters: Optional[Dict[str, Any]]) -> Tuple[sql.Composed, list]:
        if not filters:
            return sql.SQL(""), []
        clauses: list[sql.Composed] = []
        params: list = []
        for key, value in filters.items():
            clauses.append(sql.SQL("metadata->>%s = %s"))
            params.extend([key, str(value)])
        where = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(clauses)
        return where, params

    def upsert(self, ids: List[str], vectors: List[List[float]], payloads: List[Dict[str, Any]]) -> None:
        if not ids:
            return
        for m in payloads:
            if not m.get("run_id"):
                raise ValueError("metadata.run_id is required (which session this summary belongs to)")
        rows = [(i, v, Json(self._normalize_payload(p))) for i, v, p in zip(ids, vectors, payloads)]
        stmt = sql.SQL(
            "INSERT INTO {} (id, embedding, metadata) VALUES (%s, %s::vector, %s) "
            "ON CONFLICT (id) DO UPDATE SET embedding = EXCLUDED.embedding, metadata = EXCLUDED.metadata"
        ).format(sql.Identifier(self.collection))
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(stmt, rows)
            conn.commit()

    def _normalize_payload(self, p: Dict[str, Any]) -> Dict[str, Any]:
        p = dict(p)
        p.setdefault("created_at", self._now_iso())
        p.setdefault("updated_at", self._now_iso())
        p.setdefault("version", 1)
        return p

    def search(self, vector: List[float], filters: Dict[str, Any], limit: int = 10) -> List[Dict[str, Any]]:
        self._require_scope(filters)
        where, params = self._build_filter(filters)
        query = sql.SQL("SELECT id, metadata, embedding <-> %s::vector AS dist FROM {} {} ORDER BY dist LIMIT %s").format(
            sql.Identifier(self.collection), where
        )
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, [vector] + params + [limit])
                rows = cur.fetchall()
        return [{"id": r[0], "payload": r[1], "score": float(r[2])} for r in rows]

    def get(self, summary_id: str) -> Optional[Dict[str, Any]]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("SELECT id, metadata FROM {} WHERE id = %s").format(sql.Identifier(self.collection)),
                    (summary_id,),
                )
                row = cur.fetchone()
        if not row:
            return None
        return {"id": row[0], "payload": row[1]}

    def list(self, filters: Optional[Dict[str, Any]] = None, limit: int = 100) -> List[Dict[str, Any]]:
        self._require_scope(filters)
        where, params = self._build_filter(filters)
        query = sql.SQL("SELECT id, metadata FROM {} {} ORDER BY (metadata->>'updated_at') DESC LIMIT %s").format(
            sql.Identifier(self.collection), where
        )
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params + [limit])
                rows = cur.fetchall()
        return [{"id": r[0], "payload": r[1]} for r in rows]

    def count(self, filters: Optional[Dict[str, Any]] = None) -> int:
        self._require_scope(filters)
        where, params = self._build_filter(filters)
        query = sql.SQL("SELECT COUNT(*) FROM {} {}").format(sql.Identifier(self.collection), where)
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                row = cur.fetchone()
        return row[0] if row else 0

    def delete(self, ids: List[str]) -> None:
        if not ids:
            return
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("DELETE FROM {} WHERE id = ANY(%s)").format(sql.Identifier(self.collection)),
                    (list(ids),),
                )
            conn.commit()

    def delete_by_filters(self, filters: Dict[str, Any]) -> None:
        self._require_scope(filters)
        where, params = self._build_filter(filters)
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql.SQL("DELETE FROM {} {}").format(sql.Identifier(self.collection), where), params)
            conn.commit()
