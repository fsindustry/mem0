"""MemBoxStore + MemSceneStore:话题与场景 pgvector 存储。

迁移自参考库 digests/vector_store.py(Milvus→pgvector,去后缀)。
两张表与 summary 同构(id / embedding / metadata JSONB)。

== membox(会话内话题)==
metadata:user_id/agent_id, run_id, content(话题摘要), topic(核心主题),
memory_ids(关联主记忆 id), user_scene_ids/agent_scene_ids(反指 memscene), created_at/updated_at。
scope:user_id/agent_id/run_id。

== memscene(跨会话场景,多版本)==
metadata:owner_id/owner_type, scene_id(逻辑,跨版本共享), version_id, scene_name,
content, status(GENERATING/COMPLETED/FAILED), feedback, created_at/updated_at/last_update_time。
scope:owner_id。
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from psycopg import sql
from psycopg.types.json import Json

from .constants import now_iso

logger = logging.getLogger(__name__)

MEMBOX_SCOPE_KEYS = ("user_id", "agent_id", "run_id")
MEMSCENE_SCOPE_KEYS = ("owner_id",)

_SCENE_STATUS_GENERATING = "GENERATING"
_SCENE_STATUS_COMPLETED = "COMPLETED"
_SCENE_STATUS_FAILED = "FAILED"


class _BaseVectorStore:
    def __init__(self, pool, collection_name: str, dims: int = 1536) -> None:
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

    def _require_scope(self, filters: Optional[Dict[str, Any]], keys: tuple) -> None:
        if not filters or not any(filters.get(k) and filters.get(k) != "*" for k in keys):
            raise ValueError(f"query requires at least one of {keys}")

    def _build_filter(self, filters: Optional[Dict[str, Any]]) -> Tuple[sql.Composed, list]:
        if not filters:
            return sql.SQL(""), []
        clauses: list[sql.Composed] = []
        params: list = []
        for key, value in filters.items():
            clauses.append(sql.SQL("metadata->>%s = %s"))
            params.extend([key, str(value)])
        return sql.SQL(" WHERE ") + sql.SQL(" AND ").join(clauses), params

    def _normalize(self, p: Dict[str, Any]) -> Dict[str, Any]:
        p = dict(p)
        p.setdefault("created_at", now_iso())
        p.setdefault("updated_at", now_iso())
        return p


class MemBoxStore(_BaseVectorStore):
    def __init__(self, pool, collection_name: str = "mem0_membox", dims: int = 1536) -> None:
        super().__init__(pool, collection_name, dims)

    def upsert(self, ids: List[str], vectors: List[List[float]], payloads: List[Dict[str, Any]]) -> None:
        rows = [(i, v, Json(self._normalize(p))) for i, v, p in zip(ids, vectors, payloads)]
        stmt = sql.SQL(
            "INSERT INTO {} (id, embedding, metadata) VALUES (%s, %s::vector, %s) "
            "ON CONFLICT (id) DO UPDATE SET embedding = EXCLUDED.embedding, metadata = EXCLUDED.metadata"
        ).format(sql.Identifier(self.collection))
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(stmt, rows)
            conn.commit()

    def get(self, box_id: str) -> Optional[Dict[str, Any]]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("SELECT id, metadata FROM {} WHERE id = %s").format(sql.Identifier(self.collection)),
                    (box_id,),
                )
                row = cur.fetchone()
        return {"id": row[0], "payload": row[1]} if row else None

    def search(self, vector: List[float], filters: Dict[str, Any], limit: int = 10) -> List[Dict[str, Any]]:
        self._require_scope(filters, MEMBOX_SCOPE_KEYS)
        where, params = self._build_filter(filters)
        q = sql.SQL("SELECT id, metadata, embedding <-> %s::vector AS dist FROM {} {} ORDER BY dist LIMIT %s").format(
            sql.Identifier(self.collection), where
        )
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(q, [vector] + params + [limit])
                rows = cur.fetchall()
        return [{"id": r[0], "payload": r[1], "score": float(r[2])} for r in rows]

    def list(self, filters: Optional[Dict[str, Any]] = None, limit: int = 100) -> List[Dict[str, Any]]:
        self._require_scope(filters, MEMBOX_SCOPE_KEYS)
        where, params = self._build_filter(filters)
        q = sql.SQL("SELECT id, metadata FROM {} {} ORDER BY (metadata->>'updated_at') DESC LIMIT %s").format(
            sql.Identifier(self.collection), where
        )
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(q, params + [limit])
                rows = cur.fetchall()
        return [{"id": r[0], "payload": r[1]} for r in rows]

    def count(self, filters: Optional[Dict[str, Any]] = None) -> int:
        self._require_scope(filters, MEMBOX_SCOPE_KEYS)
        where, params = self._build_filter(filters)
        q = sql.SQL("SELECT COUNT(*) FROM {} {}").format(sql.Identifier(self.collection), where)
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(q, params)
                row = cur.fetchone()
        return row[0] if row else 0

    def delete(self, ids: List[str]) -> None:
        if not ids:
            return
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql.SQL("DELETE FROM {} WHERE id = ANY(%s)").format(sql.Identifier(self.collection)), (list(ids),))
            conn.commit()

    def delete_by_filters(self, filters: Dict[str, Any]) -> None:
        self._require_scope(filters, MEMBOX_SCOPE_KEYS)
        where, params = self._build_filter(filters)
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql.SQL("DELETE FROM {} {}").format(sql.Identifier(self.collection), where), params)
            conn.commit()


class MemSceneStore(_BaseVectorStore):
    def __init__(self, pool, collection_name: str = "mem0_memscene", dims: int = 1536) -> None:
        super().__init__(pool, collection_name, dims)

    def upsert(self, ids: List[str], vectors: List[List[float]], payloads: List[Dict[str, Any]]) -> None:
        rows = [(i, v, Json(self._normalize(p))) for i, v, p in zip(ids, vectors, payloads)]
        stmt = sql.SQL(
            "INSERT INTO {} (id, embedding, metadata) VALUES (%s, %s::vector, %s) "
            "ON CONFLICT (id) DO UPDATE SET embedding = EXCLUDED.embedding, metadata = EXCLUDED.metadata"
        ).format(sql.Identifier(self.collection))
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(stmt, rows)
            conn.commit()

    def get(self, record_id: str) -> Optional[Dict[str, Any]]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("SELECT id, metadata FROM {} WHERE id = %s").format(sql.Identifier(self.collection)),
                    (record_id,),
                )
                row = cur.fetchone()
        return {"id": row[0], "payload": row[1]} if row else None

    def get_many(self, record_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        if not record_ids:
            return {}
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("SELECT id, metadata FROM {} WHERE id = ANY(%s)").format(sql.Identifier(self.collection)),
                    (list(record_ids),),
                )
                rows = cur.fetchall()
        return {r[0]: {"id": r[0], "payload": r[1]} for r in rows}

    def search(self, vector: List[float], filters: Dict[str, Any], limit: int = 10) -> List[Dict[str, Any]]:
        self._require_scope(filters, MEMSCENE_SCOPE_KEYS)
        where, params = self._build_filter(filters)
        q = sql.SQL("SELECT id, metadata, embedding <-> %s::vector AS dist FROM {} {} ORDER BY dist LIMIT %s").format(
            sql.Identifier(self.collection), where
        )
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(q, [vector] + params + [limit])
                rows = cur.fetchall()
        return [{"id": r[0], "payload": r[1], "score": float(r[2])} for r in rows]

    def list(self, filters: Optional[Dict[str, Any]] = None, limit: int = 100) -> List[Dict[str, Any]]:
        self._require_scope(filters, MEMSCENE_SCOPE_KEYS)
        where, params = self._build_filter(filters)
        q = sql.SQL("SELECT id, metadata FROM {} {} ORDER BY (metadata->>'updated_at') DESC LIMIT %s").format(
            sql.Identifier(self.collection), where
        )
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(q, params + [limit])
                rows = cur.fetchall()
        return [{"id": r[0], "payload": r[1]} for r in rows]

    def count(self, filters: Optional[Dict[str, Any]] = None) -> int:
        self._require_scope(filters, MEMSCENE_SCOPE_KEYS)
        where, params = self._build_filter(filters)
        q = sql.SQL("SELECT COUNT(*) FROM {} {}").format(sql.Identifier(self.collection), where)
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(q, params)
                row = cur.fetchone()
        return row[0] if row else 0

    def list_overviews(self, owner_type: str, owner_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """取 range=ALL 总览记录(scene_id='all')。"""
        q = sql.SQL(
            "SELECT id, metadata FROM {} WHERE metadata->>'owner_type' = %s AND metadata->>'owner_id' = %s "
            "AND metadata->>'scene_id' = 'all' ORDER BY (metadata->>'updated_at') DESC LIMIT %s"
        ).format(sql.Identifier(self.collection))
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(q, [owner_type, owner_id, limit])
                rows = cur.fetchall()
        return [{"id": r[0], "payload": r[1]} for r in rows]

    def list_stale_all(self, range_flag: str, cutoff_iso: str, top_k: int = 2000) -> List[Dict[str, Any]]:
        """GC 用:取 range=ALL/ALL_SNAPSHOT 且 updated_at < cutoff 的记录。"""
        range_filter = "all" if range_flag == "ALL" else "all_snapshot"
        q = sql.SQL(
            "SELECT id, metadata FROM {} WHERE metadata->>'scene_id' = %s "
            "AND (metadata->>'updated_at')timestamp < %s::timestamptz LIMIT %s"
        ).format(sql.Identifier(self.collection))
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(q, [range_filter, cutoff_iso, top_k])
                rows = cur.fetchall()
        return [{"id": r[0], "payload": r[1]} for r in rows]

    def delete(self, record_id: str) -> bool:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql.SQL("DELETE FROM {} WHERE id = %s").format(sql.Identifier(self.collection)), (record_id,))
                n = cur.rowcount
            conn.commit()
        return bool(n)
