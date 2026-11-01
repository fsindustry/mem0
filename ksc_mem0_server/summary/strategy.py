"""SummaryStrategyStore:摘要策略读取 + state(active_snapshot_id 唯一指针)。

迁移自参考库 summary/strategy.py。摘要无 profile 的数值 version,active_snapshot_id 是
内容唯一指针;strategies_snapshot 冻结策略列表(一次摘要可多策略)。
"""

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class SummaryStrategyStore:
    def __init__(self, pool, product_id: str, strategy_store=None) -> None:
        self.pool = pool
        self.product_id = product_id
        self.strategy_store = strategy_store

    def list_active_summary_strategies(self) -> List[Dict[str, str]]:
        """读取启用的 summary 类型策略(供一次摘要 fan-out 多策略)。"""
        if self.strategy_store is not None:
            return self.strategy_store.list_active_prompts_by_type("summary")
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, name, prompt FROM mem0_strategies WHERE strategy_type = 'summary' AND status = 1"
                )
                rows = cur.fetchall()
        return [{"strategy_id": r[0], "name": r[1], "prompt": r[2] or ""} for r in rows]

    def get_active_snapshot_id(self, user_id: str, run_id: str) -> Optional[str]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT active_snapshot_id FROM summary_state WHERE user_id = %s AND run_id = %s",
                    (user_id, run_id),
                )
                row = cur.fetchone()
        return row[0] if row else None

    def get_last_summary_update(self, user_id: str, run_id: str):
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT updated_at FROM summary_state WHERE user_id = %s AND run_id = %s",
                    (user_id, run_id),
                )
                row = cur.fetchone()
        return row[0] if row else None
