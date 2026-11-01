"""MemberCatalogStore:成员反查(按 member 查所属空间,含空间元信息)。

迁移自参考库 shared_space/member_catalog.py(MySQL→PG,去后缀)。
供"成员所属空间"反查接口:给一个 user_id/agent_id,返回其所属空间列表。
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class MemberCatalogStore:
    def __init__(self, pool) -> None:
        self.pool = pool

    def list_member_spaces(self, member_id: str, member_type: str) -> List[Dict[str, Any]]:
        """查成员所属空间(含空间元信息)。"""
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT s.space_id, s.name, s.extract_enabled, m.member_id, m.member_type, m.joined_time
                    FROM shared_space_members m
                    JOIN shared_spaces s ON s.space_id = m.space_id
                    WHERE m.member_id = %s AND m.member_type = %s
                    ORDER BY m.joined_time
                    """,
                    (member_id, member_type),
                )
                rows = cur.fetchall()
        return [
            {
                "space_id": r[0], "name": r[1], "extract_enabled": bool(r[2]),
                "member_id": r[3], "member_type": r[4], "joined_time": r[5],
            }
            for r in rows
        ]

    def list_space_member_ids(self, space_id: str) -> List[Dict[str, str]]:
        """查空间所有成员。"""
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT member_id, member_type FROM shared_space_members WHERE space_id = %s",
                    (space_id,),
                )
                rows = cur.fetchall()
        return [{"member_id": r[0], "member_type": r[1]} for r in rows]
