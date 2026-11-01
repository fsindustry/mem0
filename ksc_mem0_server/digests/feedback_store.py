"""DigestsFeedbackStore:用户反馈(digests_feedback 表)。

迁移自参考库 digests/feedback_store.py(MySQL→PG,去后缀)。
仅持久化稳定字段(feedback_id/owner_type/owner_id/content)。
"""

import logging
import uuid
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class DigestsFeedbackStore:
    def __init__(self, pool, product_id: str) -> None:
        self.pool = pool
        self.product_id = product_id

    def list(self, owner_type: str, owner_id: str) -> List[Dict[str, Any]]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT feedback_id, owner_type, owner_id, content, created_at FROM digests_feedback "
                    "WHERE owner_type = %s AND owner_id = %s ORDER BY created_at ASC",
                    (owner_type, owner_id),
                )
                rows = cur.fetchall()
        return [
            {"feedback_id": r[0], "owner_type": r[1], "owner_id": r[2], "content": r[3], "created_at": r[4]}
            for r in rows
        ]

    def create(self, owner_type: str, owner_id: str, content: str) -> str:
        fid = str(uuid.uuid4())
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO digests_feedback (feedback_id, owner_type, owner_id, content) VALUES (%s, %s, %s, %s)",
                    (fid, owner_type, owner_id, content),
                )
            conn.commit()
        return fid

    def delete(self, feedback_id: str) -> bool:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM digests_feedback WHERE feedback_id = %s", (feedback_id,))
                n = cur.rowcount
            conn.commit()
        return bool(n)
