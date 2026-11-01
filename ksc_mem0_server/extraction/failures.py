"""ExtractionFailureStore:失败补偿表(PG)+ 状态机 + 退避。

迁移自参考库 extraction/failures.py(MySQL→PG,去后缀)。状态机:
  PENDING → RUNNING(CAS 抢占,owner_key)→ SUCCEEDED / DEAD(超 max_retry)
退避:next_retry_at = now() + min(BASE*2^retry, MAX)。reclaim 把超 lease 的 RUNNING 重置回 PENDING。
表名 extraction_failures(去 _{pid})。
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

RETRY_BACKOFF_BASE = 60
RETRY_BACKOFF_MAX = 3600
MAX_RETRY = 5
STALE_RUNNING_LEASE_SECONDS = 300


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _backoff_seconds(retry_count: int) -> int:
    return min(RETRY_BACKOFF_BASE * (2 ** max(0, retry_count)), RETRY_BACKOFF_MAX)


_DDL = """
CREATE TABLE IF NOT EXISTS extraction_failures (
    id               VARCHAR(64) PRIMARY KEY,
    product_id       VARCHAR(64) NOT NULL,
    episode_id       VARCHAR(64) NOT NULL,
    strategy_id      VARCHAR(64) NOT NULL,
    strategy_type    VARCHAR(64) NOT NULL DEFAULT 'fact',
    user_id          VARCHAR(64),
    agent_id         VARCHAR(64),
    run_id           VARCHAR(64),
    stage            VARCHAR(32) NOT NULL,
    error_type       VARCHAR(64) NOT NULL DEFAULT 'UNKNOWN',
    error_msg       TEXT,
    retry_count      INT NOT NULL DEFAULT 0,
    status           VARCHAR(16) NOT NULL DEFAULT 'PENDING',
    owner_key        VARCHAR(64) DEFAULT NULL,
    next_retry_at    TIMESTAMP(3) WITH TIME ZONE DEFAULT NULL,
    observation_date VARCHAR(32),
    created_at       TIMESTAMP(3) WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at       TIMESTAMP(3) WITH TIME ZONE NOT NULL DEFAULT now(),
    CONSTRAINT ef_episode_strategy UNIQUE (product_id, episode_id, strategy_id)
)
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_ef_status ON extraction_failures (status)",
    "CREATE INDEX IF NOT EXISTS idx_ef_next_retry ON extraction_failures (status, next_retry_at)",
]


class ExtractionFailureStore:
    STATUS_PENDING = "PENDING"
    STATUS_RUNNING = "RUNNING"
    STATUS_SUCCEEDED = "SUCCEEDED"
    STATUS_DEAD = "DEAD"

    def __init__(self, pool, product_id: str) -> None:
        self.pool = pool
        self.product_id = product_id
        self._create_table()

    def _create_table(self) -> None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_DDL)
                for idx in _INDEXES:
                    cur.execute(idx)
            conn.commit()

    def record(
        self,
        episode_id: str,
        strategy_id: str,
        stage: str,
        error_type: str = "UNKNOWN",
        error_msg: str = "",
        strategy_type: str = "fact",
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        observation_date: Optional[str] = None,
    ) -> str:
        """记录失败(PENDING)。同 (product, episode, strategy) 已存在则更新(RUNNING/SUCCEEDED 保留状态/租约,其余重置)。"""
        fid = str(uuid.uuid4())
        now = _now()
        obs = observation_date or now.date().isoformat()
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO extraction_failures
                    (id, product_id, episode_id, strategy_id, strategy_type, user_id, agent_id, run_id,
                     stage, error_type, error_msg, status, observation_date, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (product_id, episode_id, strategy_id) DO UPDATE SET
                        stage = EXCLUDED.stage,
                        error_type = EXCLUDED.error_type,
                        error_msg = EXCLUDED.error_msg,
                        observation_date = EXCLUDED.observation_date,
                        retry_count = CASE WHEN extraction_failures.status IN ('RUNNING','SUCCEEDED')
                                      THEN extraction_failures.retry_count ELSE 0 END,
                        next_retry_at = CASE WHEN extraction_failures.status IN ('RUNNING','SUCCEEDED')
                                        THEN extraction_failures.next_retry_at ELSE NULL END,
                        owner_key = CASE WHEN extraction_failures.status IN ('RUNNING','SUCCEEDED')
                                  THEN extraction_failures.owner_key ELSE NULL END,
                        status = CASE WHEN extraction_failures.status IN ('RUNNING','SUCCEEDED')
                               THEN extraction_failures.status ELSE %s END,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        fid, self.product_id, episode_id, strategy_id, strategy_type, user_id, agent_id, run_id,
                        stage, error_type, error_msg, self.STATUS_PENDING, obs, now, now,
                        self.STATUS_PENDING,
                    ),
                )
            conn.commit()
        return fid

    def mark_running(self, failure_id: str) -> Optional[str]:
        """CAS:PENDING → RUNNING,写 owner_key。成功返回 owner_key,失败 None。"""
        owner_key = str(uuid.uuid4())
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE extraction_failures SET status = %s, owner_key = %s, updated_at = %s
                    WHERE id = %s AND status = %s
                    """,
                    (self.STATUS_RUNNING, owner_key, _now(), failure_id, self.STATUS_PENDING),
                )
                n = cur.rowcount
            conn.commit()
        return owner_key if n else None

    def mark_succeeded(self, failure_id: str, owner_key: str) -> bool:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE extraction_failures SET status = %s, owner_key = NULL, next_retry_at = NULL, updated_at = %s
                    WHERE id = %s AND status = %s AND owner_key = %s
                    """,
                    (self.STATUS_SUCCEEDED, _now(), failure_id, self.STATUS_RUNNING, owner_key),
                )
                n = cur.rowcount
            conn.commit()
        return bool(n)

    def bump_retry(self, failure_id: str, owner_key: str) -> int:
        """结算失败尝试:retry+1,超 MAX_RETRY → DEAD,否则 PENDING + 退避。返回新 retry_count,-1 失败。"""
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT retry_count FROM extraction_failures WHERE id = %s AND status = %s AND owner_key = %s FOR UPDATE",
                    (failure_id, self.STATUS_RUNNING, owner_key),
                )
                row = cur.fetchone()
                if row is None:
                    conn.rollback()
                    return -1
                new_retry = row[0] + 1
                is_dead = new_retry >= MAX_RETRY
                new_status = self.STATUS_DEAD if is_dead else self.STATUS_PENDING
                next_retry = None if is_dead else _now() + timedelta(seconds=_backoff_seconds(new_retry))
                cur.execute(
                    """
                    UPDATE extraction_failures SET retry_count = %s, status = %s, owner_key = NULL,
                        next_retry_at = %s, updated_at = %s WHERE id = %s
                    """,
                    (new_retry, new_status, next_retry, _now(), failure_id),
                )
            conn.commit()
        return new_retry

    def reset_to_pending(self, failure_id: str) -> bool:
        """DEAD → PENDING(人工重试)。"""
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE extraction_failures SET status = %s, retry_count = 0, owner_key = NULL,
                        next_retry_at = NULL, updated_at = %s
                    WHERE id = %s AND status = %s
                    """,
                    (self.STATUS_PENDING, _now(), failure_id, self.STATUS_DEAD),
                )
                n = cur.rowcount
            conn.commit()
        return bool(n)

    def reclaim_stale_running(self) -> int:
        """超 lease 的 RUNNING 重置回 PENDING(供 scanner 调)。返回回收数。"""
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE extraction_failures SET status = %s, owner_key = NULL, updated_at = %s
                    WHERE status = %s AND updated_at < %s
                    """,
                    (self.STATUS_PENDING, _now(), self.STATUS_RUNNING, _now() - timedelta(seconds=STALE_RUNNING_LEASE_SECONDS)),
                )
                n = cur.rowcount
            conn.commit()
        return n

    def due(self, limit: int = 100) -> List[Dict[str, Any]]:
        """查待补偿:PENDING 且 next_retry_at 到期(或 NULL)。"""
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, episode_id, strategy_id, strategy_type, user_id, agent_id, run_id, stage
                    FROM extraction_failures
                    WHERE status = %s AND (next_retry_at IS NULL OR next_retry_at <= now())
                    ORDER BY next_retry_at NULLS FIRST LIMIT %s
                    """,
                    (self.STATUS_PENDING, limit),
                )
                rows = cur.fetchall()
        return [
            {"id": r[0], "episode_id": r[1], "strategy_id": r[2], "strategy_type": r[3],
             "user_id": r[4], "agent_id": r[5], "run_id": r[6], "stage": r[7]}
            for r in rows
        ]

    def list(self, status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        where = "WHERE status = %s" if status else ""
        params: list = [status] if status else []
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT id, episode_id, strategy_id, strategy_type, stage, status, retry_count, error_msg FROM extraction_failures {where} ORDER BY created_at DESC LIMIT %s",
                    params + [limit],
                )
                rows = cur.fetchall()
        return [
            {"id": r[0], "episode_id": r[1], "strategy_id": r[2], "strategy_type": r[3], "stage": r[4],
             "status": r[5], "retry_count": r[6], "error_msg": r[7]}
            for r in rows
        ]

    def delete(self, failure_id: str) -> bool:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM extraction_failures WHERE id = %s", (failure_id,))
                n = cur.rowcount
            conn.commit()
        return bool(n)

    def delete_terminal(self, status: str) -> int:
        """批量删终态(SUCCEEDED/DEAD)。"""
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM extraction_failures WHERE status = %s", (status,))
                n = cur.rowcount
            conn.commit()
        return n
