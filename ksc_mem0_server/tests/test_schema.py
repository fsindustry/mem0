"""per-product 库 schema 自愈测试:建库 + vector 扩展 + revisions + ensure_table 缺列补齐。"""

import psycopg

from ksc_mem0_server.runtime.schema import create_product_database, ensure_table, init_product_schema
from ksc_mem0_server.storage import create_pool


def _unique_pid(prefix: str) -> str:
    import uuid

    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def test_create_product_database_idempotent(pg_settings):
    pid = _unique_pid("schema-create")
    db_name = create_product_database(pid)
    assert db_name == pg_settings.product_db_name(pid)
    # 二次:已存在,幂等不报错
    create_product_database(pid)
    with psycopg.connect(pg_settings.pg_conninfo(db_name)) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        assert cur.fetchone()[0] == 1


def test_init_product_schema(pg_settings):
    pid = _unique_pid("schema-init")
    db_name = create_product_database(pid)
    pool = create_pool(pg_settings.pg_conninfo(db_name))
    try:
        init_product_schema(pool)
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            assert cur.fetchone() is not None
            cur.execute("SELECT to_regclass('revisions')")
            assert cur.fetchone()[0] == "revisions"
    finally:
        pool.close()


def test_ensure_table_adds_missing_column(pg_settings):
    pid = _unique_pid("schema-ensure")
    db_name = create_product_database(pid)
    pool = create_pool(pg_settings.pg_conninfo(db_name))
    try:
        ddl = "CREATE TABLE IF NOT EXISTS foo (id TEXT PRIMARY KEY, val TEXT)"
        ensure_table(pool, ddl, "foo", columns=[("extra", "TEXT")])
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'foo' ORDER BY ordinal_position"
            )
            assert [r[0] for r in cur.fetchall()] == ["id", "val", "extra"]
        # 二次:列已存在,幂等不重复加
        ensure_table(pool, ddl, "foo", columns=[("extra", "TEXT")])
    finally:
        pool.close()
