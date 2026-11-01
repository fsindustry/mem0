"""DigestsSettingsStore:digests 配置 + 版本链(digests_state 表)。

迁移自参考库 digests/settings_store.py(MySQL→PG,去后缀)。
owner_type+owner_id PK;memory_range/schedule_time 配置;digest_version_chain 版本链(memscene 总览记录 id 数组,newest-first)。
"""

import logging
from typing import Any, Dict, List, Optional

from psycopg.types.json import Json

logger = logging.getLogger(__name__)


class DigestsSettingsStore:
    def __init__(self, pool, product_id: str) -> None:
        self.pool = pool
        self.product_id = product_id

    def get(self, owner_type: str, owner_id: str) -> Optional[Dict[str, Any]]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT memory_range, schedule_time, digest_version_chain FROM digests_state WHERE owner_type = %s AND owner_id = %s",
                    (owner_type, owner_id),
                )
                row = cur.fetchone()
        if not row:
            return None
        return {
            "memory_range": row[0],
            "schedule_time": row[1],
            "digest_version_chain": row[2] or [],
        }

    def upsert(self, owner_type: str, owner_id: str, memory_range: str, schedule_time: Any) -> None:
        """全量覆盖配置(PUT /settings 语义)。版本链不动。"""
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO digests_state (owner_type, owner_id, memory_range, schedule_time, digest_version_chain)
                    VALUES (%s, %s, %s, %s, '[]'::jsonb)
                    ON CONFLICT (owner_type, owner_id) DO UPDATE SET
                        memory_range = EXCLUDED.memory_range,
                        schedule_time = EXCLUDED.schedule_time,
                        updated_at = now()
                    """,
                    (owner_type, owner_id, memory_range, Json(schedule_time)),
                )
            conn.commit()

    def update_version_chain(self, owner_type: str, owner_id: str, chain: List[str]) -> None:
        """生成侧在 all 摘要写入 memscene 后调:把新记录 id 追加到链首(newest-first)。"""
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO digests_state (owner_type, owner_id, digest_version_chain)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (owner_type, owner_id) DO UPDATE SET
                        digest_version_chain = EXCLUDED.digest_version_chain,
                        updated_at = now()
                    """,
                    (owner_type, owner_id, Json(chain)),
                )
            conn.commit()

    def get_version_chain(self, owner_type: str, owner_id: str) -> List[str]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT digest_version_chain FROM digests_state WHERE owner_type = %s AND owner_id = %s",
                    (owner_type, owner_id),
                )
                row = cur.fetchone()
        return row[0] if row else []
