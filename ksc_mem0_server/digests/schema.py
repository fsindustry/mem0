"""digests PG schema:membox/memscene pgvector 表 + digests_state/feedback 关系表。

迁移自参考库 digests/(MySQL→PG,去 _{pid} 后缀)。
  - membox/memscene:pgvector 表(由 store._create_col 自建,本模块只建关系表)
  - digests_state:配置 + 版本链(owner_type, owner_id PK;memory_range/schedule_time/digest_version_chain)
  - digests_feedback:用户反馈(feedback_id PK;owner_type/owner_id/content)
"""

import logging

logger = logging.getLogger(__name__)

DIGESTS_STATE_DDL = """
CREATE TABLE IF NOT EXISTS digests_state (
    owner_type            VARCHAR(16) NOT NULL,
    owner_id              VARCHAR(64) NOT NULL,
    memory_range          VARCHAR(32) NOT NULL DEFAULT 'LAST_3_MONTHS',
    schedule_time         JSONB,
    digest_version_chain JSONB,
    created_at            TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at            TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_type, owner_id)
)
"""

DIGESTS_FEEDBACK_DDL = """
CREATE TABLE IF NOT EXISTS digests_feedback (
    feedback_id  VARCHAR(64) PRIMARY KEY,
    owner_type   VARCHAR(16) NOT NULL,
    owner_id     VARCHAR(64) NOT NULL,
    content      TEXT NOT NULL,
    created_at   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
)
"""

DIGESTS_FEEDBACK_INDEX = "CREATE INDEX IF NOT EXISTS idx_df_owner ON digests_feedback (owner_type, owner_id)"


def _exec(pool, stmts):
    with pool.connection() as conn:
        with conn.cursor() as cur:
            for s in stmts:
                cur.execute(s)
        conn.commit()


def ensure_digests_schema(pool) -> None:
    """建 digests 关系表(幂等)。membox/memscene pgvector 表由各自 store 建表时自建。"""
    _exec(pool, [DIGESTS_STATE_DDL])
    _exec(pool, [DIGESTS_FEEDBACK_DDL, DIGESTS_FEEDBACK_INDEX])
    logger.info("Digests schema ensured (state/feedback)")
