"""SummaryTaskStore:summary_change_events / summary_review_runs / summary_state 租约流。

迁移自参考库 summary/task_store.py(MySQL→PG,去后缀)。K=(user_id, run_id)。
复用 profile/task_store.py 的 claim/heartbeat/publish/fail 模式,改为 run_id 维度,
strategies_snapshot 存冻结策略列表(一次摘要多策略),无 base_version(用 base_snapshot_id)。
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from psycopg.types.json import Json

logger = logging.getLogger(__name__)


class SummaryTaskStore:
    def __init__(self, pool, product_id: str, lease_seconds: int = 120, retry_limit: int = 10) -> None:
        self.pool = pool
        self.product_id = product_id
        self.lease_seconds = lease_seconds
        self.retry_limit = retry_limit

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def register(
        self,
        user: str,
        run_id: str,
        operation: str,
        inputs: Dict[str, Any],
        ready: bool = False,
        event_type: str = "INPUT_CHANGE",
    ) -> str:
        event_id = str(uuid.uuid4())
        status = "READY" if ready else "PENDING"
        input_hash = str(hash(str(inputs)))
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO summary_change_events
                    (event_id, operation_id, user_id, run_id, event_type, status, input_snapshot, input_hash)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (event_id, operation, user, run_id, event_type, status, Json(inputs), input_hash),
                )
            conn.commit()
        if ready:
            self.ready(event_id)
        return event_id

    def ready(self, event_id: str, not_before: Optional[datetime] = None) -> int:
        nb = not_before or self._now()
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT user_id, run_id FROM summary_change_events WHERE event_id = %s", (event_id,)
                )
                row = cur.fetchone()
                if row is None:
                    raise ValueError(f"event not found: {event_id}")
                user, run_id = row
                cur.execute(
                    "SELECT COALESCE(requested_generation, 0) FROM summary_state WHERE user_id = %s AND run_id = %s",
                    (user, run_id),
                )
                r = cur.fetchone()
                gen = (r[0] if r else 0) + 1
                cur.execute(
                    """
                    INSERT INTO summary_state (user_id, run_id, requested_generation)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (user_id, run_id)
                    DO UPDATE SET requested_generation = EXCLUDED.requested_generation
                    """,
                    (user, run_id, gen),
                )
                cur.execute(
                    "UPDATE summary_change_events SET status = 'READY', generation = %s, next_check_at = %s WHERE event_id = %s",
                    (gen, nb, event_id),
                )
            conn.commit()
        return gen

    def due(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT user_id, run_id, requested_generation, processed_generation, retry_count
                    FROM summary_state
                    WHERE deleted = 0 AND retry_count < %s
                      AND requested_generation > processed_generation
                      AND (lease_until IS NULL OR lease_until <= now())
                    ORDER BY next_run_at ASC LIMIT %s
                    """,
                    (self.retry_limit, limit),
                )
                rows = cur.fetchall()
        return [
            {"user_id": r[0], "run_id": r[1], "requested_generation": r[2], "processed_generation": r[3], "retry_count": r[4]}
            for r in rows
        ]

    def claim(self, user: str, run_id: str, strategies: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
        review_id = str(uuid.uuid4())
        lease_token = str(uuid.uuid4())
        now = self._now()
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT processed_generation, requested_generation, active_snapshot_id
                    FROM summary_state WHERE user_id = %s AND run_id = %s FOR UPDATE
                    """,
                    (user, run_id),
                )
                st = cur.fetchone()
                if st is None:
                    conn.rollback()
                    return None
                processed, requested, base_snapshot = st
                if requested <= processed:
                    conn.rollback()
                    return None
                cur.execute(
                    """
                    SELECT event_id, input_snapshot FROM summary_change_events
                    WHERE user_id = %s AND run_id = %s AND status = 'READY'
                      AND generation > %s AND generation <= %s
                    ORDER BY generation ASC
                    """,
                    (user, run_id, processed, requested),
                )
                ev_rows = cur.fetchall()
                inputs: list = []
                for r in ev_rows:
                    snap = r[1]
                    if isinstance(snap, list):
                        inputs.extend(snap)
                    elif snap is not None:
                        inputs.append(snap)
                input_manifest = {"events": [r[0] for r in ev_rows], "inputs": inputs}
                lease_epoch = int(now.timestamp())
                cur.execute(
                    """
                    INSERT INTO summary_review_runs
                    (review_id, user_id, run_id, lease_token, lease_epoch, base_snapshot_id,
                     from_generation, target_generation, strategies_snapshot, input_manifest, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'CLAIMED')
                    """,
                    (
                        review_id, user, run_id, lease_token, lease_epoch, base_snapshot,
                        processed, requested, Json(strategies or []), Json(input_manifest),
                    ),
                )
                cur.execute(
                    """
                    UPDATE summary_state SET lease_token = %s, lease_epoch = %s, lease_until = %s
                    WHERE user_id = %s AND run_id = %s
                    """,
                    (lease_token, lease_epoch, now + timedelta(seconds=self.lease_seconds), user, run_id),
                )
            conn.commit()
        return {
            "review_id": review_id,
            "user_id": user,
            "run_id": run_id,
            "lease_token": lease_token,
            "lease_epoch": lease_epoch,
            "base_snapshot_id": base_snapshot,
            "from_generation": processed,
            "target_generation": requested,
            "strategies": strategies or [],
            "inputs": inputs,
        }

    def heartbeat(self, run: Dict[str, Any]) -> None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE summary_state SET lease_until = now() + make_interval(secs => %s)
                    WHERE user_id = %s AND run_id = %s AND lease_token = %s
                    """,
                    (self.lease_seconds, run["user_id"], run["run_id"], run["lease_token"]),
                )
            conn.commit()

    def materialize(self, run: Dict[str, Any], manifest: Dict[str, Any]) -> None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE summary_review_runs SET status = 'MATERIALIZED', candidate_manifest = %s WHERE review_id = %s",
                    (Json(manifest), run["review_id"]),
                )
            conn.commit()

    def publish(self, run: Dict[str, Any], noop: bool = False) -> None:
        snapshot_id = run["review_id"]
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE summary_state
                    SET active_snapshot_id = %s, processed_generation = %s,
                        lease_token = NULL, lease_until = NULL, lease_epoch = 0,
                        next_run_at = now(), retry_count = 0, last_error = NULL
                    WHERE user_id = %s AND run_id = %s AND lease_token = %s AND processed_generation = %s
                    """,
                    (snapshot_id, run["target_generation"], run["user_id"], run["run_id"], run["lease_token"], run["from_generation"]),
                )
                cur.execute(
                    "UPDATE summary_change_events SET status = 'ACKED' WHERE user_id = %s AND run_id = %s AND generation <= %s",
                    (run["user_id"], run["run_id"], run["target_generation"]),
                )
                cur.execute(
                    "UPDATE summary_review_runs SET status = 'PUBLISHED' WHERE review_id = %s",
                    (run["review_id"],),
                )
            conn.commit()

    def fail(self, run: Dict[str, Any], error: str) -> None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE summary_state
                    SET lease_token = NULL, lease_until = NULL, lease_epoch = 0,
                        retry_count = retry_count + 1, last_error = %s,
                        next_run_at = now() + make_interval(secs => 60)
                    WHERE user_id = %s AND run_id = %s AND lease_token = %s
                    """,
                    (error[:1000], run["user_id"], run["run_id"], run["lease_token"]),
                )
                cur.execute(
                    "UPDATE summary_review_runs SET status = 'FAILED', retired_at = now() WHERE review_id = %s",
                    (run["review_id"],),
                )
            conn.commit()

    def is_published(self, review_id: str) -> bool:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM summary_review_runs WHERE review_id = %s", (review_id,))
                row = cur.fetchone()
        return bool(row and row[0] == "PUBLISHED")
