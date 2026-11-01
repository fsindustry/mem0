"""画像记忆 PG schema:profile_state / profile_change_events / profile_review_runs。

迁移自参考库 profile/schema.py(MySQL DDL → PG),去 _{pid} 后缀(每 product 独立库)。
PG 语法转换:DATETIME→TIMESTAMP, TINYINT→SMALLINT, JSON→JSONB,
UTC_TIMESTAMP(6)→now(), 反引号→双引号, 内联 INDEX→CREATE INDEX。
"""

import logging

logger = logging.getLogger(__name__)


def ensure_table(pool, ddl, table_name, columns=None):
    """CREATE IF NOT EXISTS + 缺列 ALTER(PG 版,内联以避免循环 import runtime.schema)。"""
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
            if columns:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = %s",
                    (table_name,),
                )
                existing = {r[0] for r in cur.fetchall()}
                for name, definition in columns:
                    if name not in existing:
                        cur.execute(f'ALTER TABLE "{table_name}" ADD COLUMN "{name}" {definition}')
        conn.commit()

PROFILE_STATE_DDL = """
CREATE TABLE IF NOT EXISTS profile_state (
    strategy_id          VARCHAR(128) NOT NULL,
    user_id              VARCHAR(64) NOT NULL,
    updated_at           TIMESTAMP(3) WITH TIME ZONE NOT NULL DEFAULT now(),
    version              INT NOT NULL DEFAULT 1,
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
    PRIMARY KEY (strategy_id, user_id)
)
"""

PROFILE_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS profile_change_events (
    event_id      VARCHAR(64) PRIMARY KEY,
    operation_id  VARCHAR(64) NOT NULL,
    user_id       VARCHAR(64) NOT NULL,
    strategy_id   VARCHAR(128) NOT NULL,
    event_type    VARCHAR(32) NOT NULL,
    status        VARCHAR(16) NOT NULL,
    generation    BIGINT NULL,
    input_snapshot JSONB NOT NULL,
    input_hash    VARCHAR(64) NOT NULL,
    created_at    TIMESTAMP(6) WITH TIME ZONE NOT NULL DEFAULT now(),
    next_check_at TIMESTAMP(6) WITH TIME ZONE NOT NULL DEFAULT now(),
    last_error    VARCHAR(1000) NULL,
    CONSTRAINT pe_operation_strategy UNIQUE (user_id, strategy_id, operation_id),
    CONSTRAINT pe_user_generation UNIQUE (user_id, strategy_id, generation)
)
"""

PROFILE_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS profile_review_runs (
    review_id          VARCHAR(64) PRIMARY KEY,
    user_id            VARCHAR(64) NOT NULL,
    strategy_id        VARCHAR(128) NOT NULL,
    lease_token        VARCHAR(64) NOT NULL,
    lease_epoch        BIGINT NOT NULL,
    base_version       INT NOT NULL,
    base_snapshot_id   VARCHAR(64) NULL,
    from_generation    BIGINT NOT NULL,
    target_generation  BIGINT NOT NULL,
    strategy_snapshot  JSONB NOT NULL,
    input_manifest      JSONB NOT NULL,
    candidate_manifest  JSONB NULL,
    status             VARCHAR(24) NOT NULL,
    created_at         TIMESTAMP(6) WITH TIME ZONE NOT NULL DEFAULT now(),
    retired_at         TIMESTAMP(6) WITH TIME ZONE NULL,
    gc_checked_at      TIMESTAMP(6) WITH TIME ZONE NOT NULL DEFAULT '1970-01-01'
)
"""

_INDEXES = {
    "profile_state": [
        "CREATE INDEX IF NOT EXISTS idx_profile_user ON profile_state (user_id)",
        "CREATE INDEX IF NOT EXISTS idx_profile_due ON profile_state (deleted, next_run_at, lease_until)",
    ],
    "profile_change_events": [
        "CREATE INDEX IF NOT EXISTS idx_pe_prepared ON profile_change_events (status, next_check_at)",
        "CREATE INDEX IF NOT EXISTS idx_pe_pending ON profile_change_events (status, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_pe_consume ON profile_change_events (user_id, strategy_id, status, generation)",
    ],
    "profile_review_runs": [
        "CREATE INDEX IF NOT EXISTS idx_pr_scope ON profile_review_runs (user_id, strategy_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_pr_gc ON profile_review_runs (status, retired_at, gc_checked_at)",
    ],
}


def _exec_indexes(pool, indexes: list[str]) -> None:
    with pool.connection() as conn:
        with conn.cursor() as cur:
            for idx in indexes:
                cur.execute(idx)
        conn.commit()


def ensure_profile_schema(pool) -> None:
    """建画像三表 + 索引(幂等自愈)。"""
    ensure_table(pool, PROFILE_STATE_DDL, "profile_state")
    _exec_indexes(pool, _INDEXES["profile_state"])
    ensure_table(pool, PROFILE_EVENTS_DDL, "profile_change_events")
    _exec_indexes(pool, _INDEXES["profile_change_events"])
    ensure_table(pool, PROFILE_RUNS_DDL, "profile_review_runs")
    _exec_indexes(pool, _INDEXES["profile_review_runs"])
    logger.info("Profile schema ensured (state/events/runs)")
