"""摘要 PG schema:summary_state / summary_change_events / summary_review_runs / revisions。

迁移自参考库 summary/schema.py(MySQL→PG),K=(user_id, run_id) session 级(非画像的 user_id+strategy_id)。
去 _{pid} 后缀。PG 语法转换同 profile/schema.py。
"""

import logging

logger = logging.getLogger(__name__)

SUMMARY_STATE_DDL = """
CREATE TABLE IF NOT EXISTS summary_state (
    user_id              VARCHAR(64) NOT NULL,
    run_id               VARCHAR(64) NOT NULL,
    updated_at           TIMESTAMP(3) WITH TIME ZONE NOT NULL DEFAULT now(),
    active_snapshot_id   VARCHAR(64) NULL,
    requested_generation BIGINT NOT NULL DEFAULT 0,
    processed_generation BIGINT NOT NULL DEFAULT 0,
    lease_token          VARCHAR(64) NULL,
    lease_epoch          BIGINT NOT NULL DEFAULT 0,
    lease_until          TIMESTAMP(6) WITH TIME ZONE NULL,
    next_run_at          TIMESTAMP(6) WITH TIME ZONE NOT NULL DEFAULT '1970-01-01',
    retry_count          INT NOT NULL DEFAULT 0,
    last_error           VARCHAR(1000) NULL,
    migration_state      VARCHAR(16) NOT NULL DEFAULT 'LEGACY',
    deleted              SMALLINT NOT NULL DEFAULT 0,
    legacy_retired_at    TIMESTAMP(6) WITH TIME ZONE NULL,
    PRIMARY KEY (user_id, run_id)
)
"""

SUMMARY_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS summary_change_events (
    event_id      VARCHAR(64) PRIMARY KEY,
    operation_id  VARCHAR(64) NOT NULL,
    user_id       VARCHAR(64) NOT NULL,
    run_id        VARCHAR(64) NOT NULL,
    event_type    VARCHAR(32) NOT NULL,
    status        VARCHAR(16) NOT NULL,
    generation    BIGINT NULL,
    input_snapshot JSONB NOT NULL,
    input_hash    VARCHAR(64) NOT NULL,
    created_at    TIMESTAMP(6) WITH TIME ZONE NOT NULL DEFAULT now(),
    next_check_at TIMESTAMP(6) WITH TIME ZONE NOT NULL DEFAULT now(),
    last_error    VARCHAR(1000) NULL,
    CONSTRAINT se_operation_run UNIQUE (user_id, run_id, operation_id),
    CONSTRAINT se_user_generation UNIQUE (user_id, run_id, generation)
)
"""

SUMMARY_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS summary_review_runs (
    review_id          VARCHAR(64) PRIMARY KEY,
    user_id            VARCHAR(64) NOT NULL,
    run_id             VARCHAR(64) NOT NULL,
    lease_token        VARCHAR(64) NOT NULL,
    lease_epoch        BIGINT NOT NULL,
    base_snapshot_id   VARCHAR(64) NULL,
    from_generation    BIGINT NOT NULL,
    target_generation  BIGINT NOT NULL,
    strategies_snapshot JSONB NOT NULL,
    input_manifest      JSONB NOT NULL,
    candidate_manifest  JSONB NULL,
    status             VARCHAR(24) NOT NULL,
    created_at         TIMESTAMP(6) WITH TIME ZONE NOT NULL DEFAULT now(),
    retired_at         TIMESTAMP(6) WITH TIME ZONE NULL,
    gc_checked_at      TIMESTAMP(6) WITH TIME ZONE NOT NULL DEFAULT '1970-01-01'
)
"""

_INDEXES = {
    "summary_state": [
        "CREATE INDEX IF NOT EXISTS idx_summary_user ON summary_state (user_id)",
        "CREATE INDEX IF NOT EXISTS idx_summary_due ON summary_state (deleted, next_run_at, lease_until)",
    ],
    "summary_change_events": [
        "CREATE INDEX IF NOT EXISTS idx_se_prepared ON summary_change_events (status, next_check_at)",
        "CREATE INDEX IF NOT EXISTS idx_se_pending ON summary_change_events (status, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_se_consume ON summary_change_events (user_id, run_id, status, generation)",
    ],
    "summary_review_runs": [
        "CREATE INDEX IF NOT EXISTS idx_sr_scope ON summary_review_runs (user_id, run_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_sr_gc ON summary_review_runs (status, retired_at, gc_checked_at)",
    ],
}


def _ensure_table(pool, ddl, table_name):
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()


def _exec_indexes(pool, indexes):
    with pool.connection() as conn:
        with conn.cursor() as cur:
            for idx in indexes:
                cur.execute(idx)
        conn.commit()


def ensure_summary_schema(pool) -> None:
    _ensure_table(pool, SUMMARY_STATE_DDL, "summary_state")
    _exec_indexes(pool, _INDEXES["summary_state"])
    _ensure_table(pool, SUMMARY_EVENTS_DDL, "summary_change_events")
    _exec_indexes(pool, _INDEXES["summary_change_events"])
    _ensure_table(pool, SUMMARY_RUNS_DDL, "summary_review_runs")
    _exec_indexes(pool, _INDEXES["summary_review_runs"])
    logger.info("Summary schema ensured (state/events/runs)")
