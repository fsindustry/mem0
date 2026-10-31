"""per-product 库的 schema 自愈:建库 + 建扩展 + 建关系表。

P0 仅建 vector 扩展与 revisions 表;history/messages 由 PostgreSQLHistoryManager
自建,pgvector 向量表由 PGVector.create_col 自建。P1+ 各子系统 store 在此追加建表,
并复用 ensure_table 工具做"CREATE IF NOT EXISTS + 缺列 ALTER"自愈(蓝本
release-v2.2 ksc-mem0/mem0_server/db_schema.py,改 PG 语法)。
"""

import logging

import psycopg

from ..config import get_settings

logger = logging.getLogger(__name__)


def create_product_database(product_id: str) -> str:
    """创建该 product 的独立 PG 库(若不存在),返回库名。

    PG 无 `CREATE DATABASE IF NOT EXISTS`,需先查 pg_database;且 CREATE DATABASE
    不能在事务块内,故连 maintenance 库用 autocommit。
    """
    s = get_settings()
    db_name = s.product_db_name(product_id)
    with psycopg.connect(s.pg_conninfo(s.maintenance_db), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            if cur.fetchone() is None:
                try:
                    cur.execute(f'CREATE DATABASE "{db_name}"')
                    logger.info("Created product database: %s", db_name)
                except psycopg.errors.DuplicateDatabase:
                    # 跨 worker 竞态:另一进程已建,幂等忽略
                    pass
    return db_name


def init_product_schema(pool) -> None:
    """在新 product 库内建 vector 扩展与 revisions 表(幂等)。"""
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS revisions (
                    feature    VARCHAR(64) PRIMARY KEY,
                    revision   BIGINT NOT NULL DEFAULT 0,
                    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
                )
                """
            )
        conn.commit()
    logger.info("Product schema initialized (vector ext + revisions)")


def ensure_table(pool, ddl: str, table_name: str, columns: list | None = None) -> None:
    """通用表自愈:CREATE IF NOT EXISTS + 缺列 ALTER(PG 版,供 P1+ 各 store)。

    Args:
        ddl: 完整 CREATE TABLE IF NOT EXISTS 语句。
        table_name: 表名(用于缺列检测)。
        columns: [(列名, 列定义)] 用于 ALTER ADD;None 则跳过缺列补齐。
    """
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
            if columns:
                cur.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = current_schema() AND table_name = %s
                    """,
                    (table_name,),
                )
                existing = {r[0] for r in cur.fetchall()}
                for name, definition in columns:
                    if name not in existing:
                        cur.execute(f'ALTER TABLE "{table_name}" ADD COLUMN "{name}" {definition}')
                        logger.info("[schema] column added table=%s column=%s", table_name, name)
        conn.commit()
