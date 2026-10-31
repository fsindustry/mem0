"""PostgreSQL 历史管理器:替代 mem0 的 SQLiteManager,接口与返回格式严格对齐。

mem0 Memory 在 __init__/reset/close 中硬编码 `self.db = SQLiteManager(...)`;
FactsMemory(Memory 子类)在 super().__init__ 后将 self.db 替换为本类实例。
因此本类必须与 SQLiteManager(mem0/memory/storage.py)逐一兼容:
  - 建表:history / messages
  - 方法:add_history / batch_add_history / get_history / save_messages / get_last_messages / reset / close
  - 返回格式:get_history / get_last_messages 的 dict 字段与类型

存储语义选择:列类型用 TEXT / SMALLINT,created_at/updated_at 存 ISO 8601 字符串,
排序用字符串字典序(ISO 8601 定长前缀,字典序 == 时间序)——完全复刻 SQLite 行为,
避免 PG TIMESTAMPTZ 与 SQLite TEXT 间的类型/时区差异引入 mem0 既有逻辑的回归。
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS history (
    id           TEXT PRIMARY KEY,
    memory_id    TEXT,
    old_memory   TEXT,
    new_memory   TEXT,
    event        TEXT,
    created_at   TEXT,
    updated_at   TEXT,
    is_deleted   SMALLINT,
    actor_id     TEXT,
    role         TEXT
)
"""

_MESSAGES_DDL = """
CREATE TABLE IF NOT EXISTS messages (
    id            TEXT PRIMARY KEY,
    session_scope TEXT,
    role          TEXT,
    content       TEXT,
    name          TEXT,
    created_at    TEXT
)
"""


class PostgreSQLHistoryManager:
    """与 SQLiteManager 接口对齐的 PostgreSQL 历史/消息存储。

    不持有连接池的所有权(连接池由 ProductRuntime 管理,多 store 共享);
    close() 为 no-op,避免误关共享 pool。
    """

    def __init__(self, pool) -> None:
        self.pool = pool
        self._closed = False
        self._create_history_table()
        self._create_messages_table()

    # --- 建表 ---
    def _create_history_table(self) -> None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_HISTORY_DDL)
            conn.commit()

    def _create_messages_table(self) -> None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_MESSAGES_DDL)
            conn.commit()

    # --- history ---
    def add_history(
        self,
        memory_id: str,
        old_memory: Optional[str],
        new_memory: Optional[str],
        event: str,
        *,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
        is_deleted: int = 0,
        actor_id: Optional[str] = None,
        role: Optional[str] = None,
    ) -> None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO history (
                        id, memory_id, old_memory, new_memory, event,
                        created_at, updated_at, is_deleted, actor_id, role
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(uuid.uuid4()),
                        memory_id,
                        old_memory,
                        new_memory,
                        event,
                        created_at,
                        updated_at,
                        is_deleted,
                        actor_id,
                        role,
                    ),
                )
            conn.commit()

    def batch_add_history(self, records: List[Dict[str, Any]]) -> None:
        if not records:
            return
        rows = [
            (
                str(uuid.uuid4()),
                r.get("memory_id"),
                r.get("old_memory"),
                r.get("new_memory"),
                r.get("event"),
                r.get("created_at"),
                r.get("updated_at"),
                r.get("is_deleted", 0),
                r.get("actor_id"),
                r.get("role"),
            )
            for r in records
        ]
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO history (
                        id, memory_id, old_memory, new_memory, event,
                        created_at, updated_at, is_deleted, actor_id, role
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    rows,
                )
            conn.commit()

    def get_history(self, memory_id: str) -> List[Dict[str, Any]]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, memory_id, old_memory, new_memory, event,
                           created_at, updated_at, is_deleted, actor_id, role
                    FROM history
                    WHERE memory_id = %s
                    ORDER BY created_at ASC, updated_at ASC
                    """,
                    (memory_id,),
                )
                rows = cur.fetchall()
        return [
            {
                "id": r[0],
                "memory_id": r[1],
                "old_memory": r[2],
                "new_memory": r[3],
                "event": r[4],
                "created_at": r[5],
                "updated_at": r[6],
                "is_deleted": bool(r[7]),
                "actor_id": r[8],
                "role": r[9],
            }
            for r in rows
        ]

    # --- messages ---
    def save_messages(self, messages: List[Dict[str, Any]], session_scope: str) -> None:
        if not messages:
            return
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            (
                str(uuid.uuid4()),
                session_scope,
                m.get("role"),
                m.get("content"),
                m.get("name"),
                now,
            )
            for m in messages
        ]
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO messages (id, session_scope, role, content, name, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    rows,
                )
                # Evict old messages beyond the most recent 10 for this scope.
                cur.execute(
                    """
                    DELETE FROM messages WHERE session_scope = %s AND id NOT IN (
                        SELECT id FROM (
                            SELECT id FROM messages WHERE session_scope = %s
                            ORDER BY created_at DESC LIMIT 10
                        ) AS keep
                    )
                    """,
                    (session_scope, session_scope),
                )
            conn.commit()

    def get_last_messages(self, session_scope: str, limit: int = 10) -> List[Dict[str, Any]]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT role, content, name, created_at FROM (
                        SELECT role, content, name, created_at
                        FROM messages
                        WHERE session_scope = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                    ) AS latest ORDER BY created_at ASC
                    """,
                    (session_scope, limit),
                )
                rows = cur.fetchall()
        return [
            {
                "role": r[0],
                "content": r[1],
                "name": r[2],
                "created_at": r[3],
            }
            for r in rows
        ]

    # --- 生命周期 ---
    def reset(self) -> None:
        """Drop and recreate the history and messages tables."""
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DROP TABLE IF EXISTS history")
                cur.execute("DROP TABLE IF EXISTS messages")
            conn.commit()
        self._create_history_table()
        self._create_messages_table()

    def close(self) -> None:
        """No-op:连接池由 ProductRuntime 统一管理,history manager 不拥有 pool。"""
        self._closed = True
