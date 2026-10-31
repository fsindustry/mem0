"""ProfileStrategyStore:profile_state 表的版本/游标管理 + 画像策略读取。

迁移自参考库 profile/strategy.py(MySQL→PG,去后缀)。
- record_profile_update:更新游标 + version+1(画像发布时调)
- advance_profile_cursor:只推游标不增 version
- get_profile_version / get_user_profile_versions:读当前版本
- list_active_profile_strategies:读启用的 profile 类型策略
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class ProfileStrategyStore:
    def __init__(self, pool, product_id: str, strategy_store=None) -> None:
        self.pool = pool
        self.product_id = product_id
        self.strategy_store = strategy_store

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def get_last_profile_update(self, user_id: str, strategy_id: str) -> Optional[datetime]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT updated_at FROM profile_state WHERE strategy_id = %s AND user_id = %s",
                    (strategy_id, user_id),
                )
                row = cur.fetchone()
        return row[0] if row else None

    def get_profile_version(self, user_id: str, strategy_id: str) -> int:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT version FROM profile_state WHERE strategy_id = %s AND user_id = %s",
                    (strategy_id, user_id),
                )
                row = cur.fetchone()
        return row[0] if row else 0

    def get_user_profile_versions(self, user_id: str) -> Dict[str, int]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT strategy_id, version FROM profile_state WHERE user_id = %s", (user_id,))
                rows = cur.fetchall()
        return {r[0]: r[1] for r in rows}

    def get_all_profile_versions(self) -> Dict[tuple, int]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id, strategy_id, version FROM profile_state")
                rows = cur.fetchall()
        return {(r[0], r[1]): r[2] for r in rows}

    def record_profile_update(self, user_id: str, strategy_id: str, t: Optional[datetime] = None) -> int:
        """更新游标 + version+1。返回更新后的 version。"""
        now = t or self._now()
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO profile_state (strategy_id, user_id, updated_at, version)
                    VALUES (%s, %s, %s, 1)
                    ON CONFLICT (strategy_id, user_id)
                    DO UPDATE SET updated_at = EXCLUDED.updated_at, version = profile_state.version + 1
                    RETURNING version
                    """,
                    (strategy_id, user_id, now),
                )
                row = cur.fetchone()
            conn.commit()
        return row[0] if row else 1

    def advance_profile_cursor(self, user_id: str, strategy_id: str, t: Optional[datetime] = None) -> None:
        """只推游标(updated_at),不增 version。"""
        now = t or self._now()
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO profile_state (strategy_id, user_id, updated_at, version)
                    VALUES (%s, %s, %s, 1)
                    ON CONFLICT (strategy_id, user_id)
                    DO UPDATE SET updated_at = EXCLUDED.updated_at
                    """,
                    (strategy_id, user_id, now),
                )
            conn.commit()

    def delete_strategy_state(self, strategy_id: str) -> None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM profile_state WHERE strategy_id = %s", (strategy_id,))
            conn.commit()

    def list_active_profile_strategies(self) -> List[Dict[str, str]]:
        if self.strategy_store is not None:
            return self.strategy_store.list_active_prompts_by_type("profile")
        # 回退直查 mem0_strategies
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, name, prompt FROM mem0_strategies WHERE strategy_type = 'profile' AND status = 1"
                )
                rows = cur.fetchall()
        return [{"strategy_id": r[0], "name": r[1], "prompt": r[2] or ""} for r in rows]
