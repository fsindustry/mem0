"""ProfileTaskStore:profile_change_events / profile_review_runs / profile_state 的租约与事件流。

迁移自参考库 profile/task_store.py(MySQL→PG,去后缀)。核心事件+租约机制:
  - register:登记事件(PENDING,generation NULL)
  - ready:分配 generation + state.requested_generation+1 + 置 READY
  - due:查待处理(state.requested>processed AND retry<limit AND 租约空闲)
  - claim:领租约(INSERT run + UPDATE state lease),返回 run(含 inputs/target_generation)
  - heartbeat:续租约
  - materialize:写候选 manifest
  - publish:落盘成功(state.version+1, processed=target, lease 释放, events ACKED)
  - fail:失败(state 租约释放, retry+1, run FAILED)
GC / retired_legacy 等留 P1b-3。
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from psycopg.types.json import Json

logger = logging.getLogger(__name__)

_DEFAULT_STRATEGY = "default"


class ProfileTaskStore:
    def __init__(self, pool, product_id: str, lease_seconds: int = 120, retry_limit: int = 10) -> None:
        self.pool = pool
        self.product_id = product_id
        self.lease_seconds = lease_seconds
        self.retry_limit = retry_limit

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    # --- 事件注册 ---
    def register(
        self,
        user: str,
        strategy: str,
        operation: str,
        inputs: Dict[str, Any],
        ready: bool = False,
        event_type: str = "INPUT_CHANGE",
    ) -> str:
        event_id = str(uuid.uuid4())
        status = "READY" if ready else "PENDING"
        input_hash = str(hash(str(inputs)))  # 轻量哈希,参考库用更稳哈希;P1b-3 可换
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO profile_change_events
                    (event_id, operation_id, user_id, strategy_id, event_type, status, input_snapshot, input_hash)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (event_id, operation, user, strategy, event_type, status, Json(inputs), input_hash),
                )
            conn.commit()
        if ready:
            self.ready(event_id)
        return event_id

    def ready(self, event_id: str, not_before: Optional[datetime] = None) -> int:
        """分配 generation + state.requested_generation+1,置 event READY。返回 generation。"""
        nb = not_before or self._now()
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT user_id, strategy_id FROM profile_change_events WHERE event_id = %s", (event_id,)
                )
                row = cur.fetchone()
                if row is None:
                    raise ValueError(f"event not found: {event_id}")
                user, strategy = row
                cur.execute(
                    "SELECT COALESCE(requested_generation, 0) FROM profile_state WHERE strategy_id = %s AND user_id = %s",
                    (strategy, user),
                )
                r = cur.fetchone()
                gen = (r[0] if r else 0) + 1
                cur.execute(
                    """
                    INSERT INTO profile_state (strategy_id, user_id, requested_generation)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (strategy_id, user_id)
                    DO UPDATE SET requested_generation = EXCLUDED.requested_generation
                    """,
                    (strategy, user, gen),
                )
                cur.execute(
                    """
                    UPDATE profile_change_events
                    SET status = 'READY', generation = %s, next_check_at = %s
                    WHERE event_id = %s
                    """,
                    (gen, nb, event_id),
                )
            conn.commit()
        return gen

    # --- 到期查询 ---
    def due(self, limit: int = 100) -> List[Dict[str, Any]]:
        """查待处理:(requested>processed) AND retry<limit AND 租约空闲(无或过期)AND not deleted。"""
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT strategy_id, user_id, requested_generation, processed_generation, retry_count
                    FROM profile_state
                    WHERE deleted = 0
                      AND retry_count < %s
                      AND requested_generation > processed_generation
                      AND (lease_until IS NULL OR lease_until <= now())
                    ORDER BY next_run_at ASC
                    LIMIT %s
                    """,
                    (self.retry_limit, limit),
                )
                rows = cur.fetchall()
        return [
            {
                "strategy_id": r[0],
                "user_id": r[1],
                "requested_generation": r[2],
                "processed_generation": r[3],
                "retry_count": r[4],
            }
            for r in rows
        ]

    # --- 领租约 ---
    def claim(self, user: str, strategy: str) -> Optional[Dict[str, Any]]:
        """领取租约:从最新 READY event 取 inputs,INSERT run,UPDATE state lease。"""
        review_id = str(uuid.uuid4())
        lease_token = str(uuid.uuid4())
        now = self._now()
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT version, processed_generation, requested_generation, active_snapshot_id
                    FROM profile_state WHERE strategy_id = %s AND user_id = %s FOR UPDATE
                    """,
                    (strategy, user),
                )
                st = cur.fetchone()
                if st is None:
                    conn.rollback()
                    return None
                base_version, processed, requested, base_snapshot = st
                if requested <= processed:
                    conn.rollback()
                    return None
                # 取该 generation 范围内 READY 事件的 inputs 聚合
                cur.execute(
                    """
                    SELECT event_id, input_snapshot FROM profile_change_events
                    WHERE user_id = %s AND strategy_id = %s AND status = 'READY'
                      AND generation > %s AND generation <= %s
                    ORDER BY generation ASC
                    """,
                    (user, strategy, processed, requested),
                )
                ev_rows = cur.fetchall()
                inputs = [r[1] for r in ev_rows]
                input_manifest = {"events": [r[0] for r in ev_rows], "inputs": inputs}
                lease_epoch = int(now.timestamp())
                cur.execute(
                    """
                    INSERT INTO profile_review_runs
                    (review_id, user_id, strategy_id, lease_token, lease_epoch, base_version,
                     base_snapshot_id, from_generation, target_generation, strategy_snapshot,
                     input_manifest, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'CLAIMED')
                    """,
                    (
                        review_id, user, strategy, lease_token, lease_epoch, base_version,
                        base_snapshot, processed, requested, Json({}), Json(input_manifest),
                    ),
                )
                cur.execute(
                    """
                    UPDATE profile_state
                    SET lease_token = %s, lease_epoch = %s, lease_until = %s
                    WHERE strategy_id = %s AND user_id = %s
                    """,
                    (lease_token, lease_epoch, now + timedelta(seconds=self.lease_seconds), strategy, user),
                )
            conn.commit()
        return {
            "review_id": review_id,
            "user_id": user,
            "strategy_id": strategy,
            "lease_token": lease_token,
            "lease_epoch": lease_epoch,
            "base_version": base_version,
            "base_snapshot_id": base_snapshot,
            "from_generation": processed,
            "target_generation": requested,
            "inputs": inputs,
        }

    def heartbeat(self, run: Dict[str, Any]) -> None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE profile_state SET lease_until = now() + make_interval(secs => %s)
                    WHERE strategy_id = %s AND user_id = %s AND lease_token = %s
                    """,
                    (self.lease_seconds, run["strategy_id"], run["user_id"], run["lease_token"]),
                )
            conn.commit()

    def materialize(self, run: Dict[str, Any], manifest: Dict[str, Any]) -> None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE profile_review_runs
                    SET status = 'MATERIALIZED', candidate_manifest = %s
                    WHERE review_id = %s
                    """,
                    (Json(manifest), run["review_id"]),
                )
            conn.commit()

    def publish(self, run: Dict[str, Any], snapshot_id: str, noop: bool = False) -> None:
        """落盘成功:state.version+1(非 noop), processed=target, active_snapshot_id, 租约释放;events ACKED。"""
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE profile_state
                    SET version = CASE WHEN %s THEN version ELSE version + 1 END,
                        active_snapshot_id = %s,
                        processed_generation = %s,
                        lease_token = NULL, lease_until = NULL, lease_epoch = 0,
                        next_run_at = now(), retry_count = 0, last_error = NULL
                    WHERE strategy_id = %s AND user_id = %s
                      AND lease_token = %s AND processed_generation = %s
                    """,
                    (
                        noop, snapshot_id, run["target_generation"],
                        run["strategy_id"], run["user_id"], run["lease_token"], run["from_generation"],
                    ),
                )
                cur.execute(
                    """
                    UPDATE profile_change_events SET status = 'ACKED'
                    WHERE user_id = %s AND strategy_id = %s AND generation <= %s
                    """,
                    (run["user_id"], run["strategy_id"], run["target_generation"]),
                )
                cur.execute(
                    "UPDATE profile_review_runs SET status = 'PUBLISHED' WHERE review_id = %s",
                    (run["review_id"],),
                )
            conn.commit()

    def fail(self, run: Dict[str, Any], error: str) -> None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE profile_state
                    SET lease_token = NULL, lease_until = NULL, lease_epoch = 0,
                        retry_count = retry_count + 1, last_error = %s,
                        next_run_at = now() + make_interval(secs => 60)
                    WHERE strategy_id = %s AND user_id = %s AND lease_token = %s
                    """,
                    (error[:1000], run["strategy_id"], run["user_id"], run["lease_token"]),
                )
                cur.execute(
                    "UPDATE profile_review_runs SET status = 'FAILED', retired_at = now() WHERE review_id = %s",
                    (run["review_id"],),
                )
            conn.commit()

    def is_published(self, review_id: str) -> bool:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status FROM profile_review_runs WHERE review_id = %s", (review_id,)
                )
                row = cur.fetchone()
        return bool(row and row[0] == "PUBLISHED")
