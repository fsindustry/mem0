"""shared_space PG schema:5 张关系表(spaces/rules/members/pending/batches)。

迁移自参考库 shared_space/store.py 的 DDL(MySQL→PG,去 _{pid} 后缀)。
共享记忆不入关系表,由 SharedVectorStore pgvector 单独存(mem0_shared)。
PG 语法转换:DATETIME→TIMESTAMP, TINYINT→SMALLINT, MEDIUMTEXT→TEXT,
反引号→双引号, 内联 KEY→CREATE INDEX, CURRENT_TIMESTAMP→now()。
"""

import logging

logger = logging.getLogger(__name__)

SPACES_DDL = """
CREATE TABLE IF NOT EXISTS shared_spaces (
    space_id           VARCHAR(64) NOT NULL,
    name               VARCHAR(256) NOT NULL,
    instance_id        VARCHAR(64) NOT NULL DEFAULT '',
    instance_name      VARCHAR(256) NOT NULL DEFAULT '',
    extract_enabled    SMALLINT NOT NULL DEFAULT 0,
    extract_prompt     TEXT NULL,
    last_enabled_time  TIMESTAMP(3) WITH TIME ZONE NULL,
    last_disabled_time TIMESTAMP(3) WITH TIME ZONE NULL,
    last_update_time   TIMESTAMP(3) WITH TIME ZONE NULL,
    cursor_time        TIMESTAMP(3) WITH TIME ZONE NULL,
    last_extract_time  TIMESTAMP(3) WITH TIME ZONE NULL,
    create_time        TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    pending_tokens     BIGINT NOT NULL DEFAULT 0,
    token_threshold    INT NULL,
    next_run_at        TIMESTAMP(3) WITH TIME ZONE NULL,
    run_lease_until     TIMESTAMP(3) WITH TIME ZONE NULL,
    run_lease_token    VARCHAR(64) NULL,
    PRIMARY KEY (space_id),
    CONSTRAINT ss_spaces_uk_name UNIQUE (name)
)
"""

RULES_DDL = """
CREATE TABLE IF NOT EXISTS shared_space_rules (
    rule_id         VARCHAR(64) NOT NULL,
    space_id        VARCHAR(64) NOT NULL,
    content         TEXT NOT NULL,
    create_time     TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    last_update_time TIMESTAMP(3) WITH TIME ZONE NULL,
    PRIMARY KEY (rule_id)
)
"""

MEMBERS_DDL = """
CREATE TABLE IF NOT EXISTS shared_space_members (
    space_id    VARCHAR(64) NOT NULL,
    member_id   VARCHAR(256) NOT NULL,
    member_type VARCHAR(16) NOT NULL,
    joined_time TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    PRIMARY KEY (space_id, member_id, member_type)
)
"""

PENDING_DDL = """
CREATE TABLE IF NOT EXISTS shared_space_fact_pending (
    space_id          VARCHAR(64) NOT NULL,
    fact_id           VARCHAR(64) NOT NULL,
    source_version    VARCHAR(64) NOT NULL,
    status            VARCHAR(16) NOT NULL DEFAULT 'pending',
    tokens            INT NOT NULL DEFAULT 0,
    source_created_at TIMESTAMP(3) WITH TIME ZONE NULL,
    user_id           VARCHAR(256) NOT NULL DEFAULT '',
    agent_id          VARCHAR(256) NOT NULL DEFAULT '',
    batch_id          VARCHAR(64) NULL,
    create_time       TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    update_time       TIMESTAMP(3) WITH TIME ZONE NULL,
    PRIMARY KEY (space_id, fact_id, source_version)
)
"""

BATCHES_DDL = """
CREATE TABLE IF NOT EXISTS shared_space_batches (
    batch_id        VARCHAR(64) NOT NULL,
    space_id        VARCHAR(64) NOT NULL,
    status          VARCHAR(16) NOT NULL DEFAULT 'running',
    rounds          INT NOT NULL DEFAULT 0,
    retries         INT NOT NULL DEFAULT 0,
    input_snapshot  TEXT NULL,
    output_snapshot TEXT NULL,
    claimed_at      TIMESTAMP(3) WITH TIME ZONE NULL,
    lease_until     TIMESTAMP(3) WITH TIME ZONE NULL,
    create_time     TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    update_time     TIMESTAMP(3) WITH TIME ZONE NULL,
    PRIMARY KEY (batch_id)
)
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_ss_spaces_extract ON shared_spaces (extract_enabled)",
    "CREATE INDEX IF NOT EXISTS idx_ss_rules_space ON shared_space_rules (space_id, create_time)",
    "CREATE INDEX IF NOT EXISTS idx_ss_members_member ON shared_space_members (member_id, member_type)",
    "CREATE INDEX IF NOT EXISTS idx_ss_pending_space ON shared_space_fact_pending (space_id, status, source_created_at, fact_id)",
    "CREATE INDEX IF NOT EXISTS idx_ss_pending_status ON shared_space_fact_pending (status)",
    "CREATE INDEX IF NOT EXISTS idx_ss_pending_fact ON shared_space_fact_pending (fact_id)",
    "CREATE INDEX IF NOT EXISTS idx_ss_batches_space ON shared_space_batches (space_id, status)",
]


def ensure_shared_space_schema(pool) -> None:
    """建 5 张关系表 + 索引(幂等)。共享记忆 pgvector 表由 SharedVectorStore 自建。"""
    with pool.connection() as conn:
        with conn.cursor() as cur:
            for ddl in (SPACES_DDL, RULES_DDL, MEMBERS_DDL, PENDING_DDL, BATCHES_DDL):
                cur.execute(ddl)
            for idx in _INDEXES:
                cur.execute(idx)
        conn.commit()
    logger.info("Shared space schema ensured (spaces/rules/members/pending/batches)")
