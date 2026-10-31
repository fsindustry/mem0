"""测试公共 fixture:PostgreSQL 连接与库隔离。

约定:测试 PG 由 docker-compose 起(`docker-compose up -d postgres`,默认 localhost:5433),
或用 TEST_POSTGRES_* 环境变量指向其它 PG。每个测试用唯一 app_db + 独立 product 库,
测试后清理(sweep PRODUCT_DB_PREFIX 前缀库 + drop app_db),互不污染。
"""

import os
import uuid

import psycopg
import pytest
from psycopg import sql

TEST_PG_HOST = os.environ.get("TEST_POSTGRES_HOST", "localhost")
TEST_PG_PORT = int(os.environ.get("TEST_POSTGRES_PORT", "5433"))
TEST_PG_USER = os.environ.get("TEST_POSTGRES_USER", "postgres")
TEST_PG_PASSWORD = os.environ.get("TEST_POSTGRES_PASSWORD", "postgres")


def _maintenance_conninfo() -> str:
    return f"postgresql://{TEST_PG_USER}:{TEST_PG_PASSWORD}@{TEST_PG_HOST}:{TEST_PG_PORT}/postgres"


def _connect_maintenance():
    return psycopg.connect(_maintenance_conninfo(), autocommit=True)


def _create_db(dbname: str) -> None:
    with _connect_maintenance() as conn, conn.cursor() as cur:
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))


def _drop_db(dbname: str) -> None:
    with _connect_maintenance() as conn, conn.cursor() as cur:
        cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname)))


def _sweep_dbs(prefix: str) -> None:
    with _connect_maintenance() as conn, conn.cursor() as cur:
        cur.execute("SELECT datname FROM pg_database WHERE datname LIKE %s", (prefix + "%",))
        for (name,) in cur.fetchall():
            cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def pg_settings(monkeypatch):
    """配置 settings 指向测试 PG,建唯一 app_db,重建 db engine 与 registry。"""
    monkeypatch.setenv("POSTGRES_HOST", TEST_PG_HOST)
    monkeypatch.setenv("POSTGRES_PORT", str(TEST_PG_PORT))
    monkeypatch.setenv("POSTGRES_USER", TEST_PG_USER)
    monkeypatch.setenv("POSTGRES_PASSWORD", TEST_PG_PASSWORD)
    monkeypatch.setenv("AUTH_DISABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("APP_DB_NAME", f"mem0_app_test_{uuid.uuid4().hex[:8]}")
    monkeypatch.setenv("PRODUCT_DB_PREFIX", "mem0_test_")

    from ksc_mem0_server import config, db
    from ksc_mem0_server.runtime import reset_registry

    config.reset_settings()
    s = config.get_settings()

    # 重建 db engine 指向新 app_db(模块级 engine 在 import 时绑定旧值)
    import sqlalchemy

    db.engine.dispose()
    db.engine = sqlalchemy.create_engine(db._app_db_url(), pool_pre_ping=True)
    db.SessionLocal.configure(bind=db.engine)
    db.init_app_db()  # 建应用库 + 表

    reset_registry()
    yield s

    reset_registry()
    _sweep_dbs(s.product_db_prefix)
    db.engine.dispose()
    _drop_db(s.app_db_name)
    config.reset_settings()


@pytest.fixture
def history_pool(pg_settings):
    """建一个临时库 + 连接池,供 PostgreSQLHistoryManager 测试。"""
    dbname = pg_settings.product_db_name(f"hist-{uuid.uuid4().hex[:8]}")
    _create_db(dbname)
    from ksc_mem0_server.storage import create_pool

    pool = create_pool(pg_settings.pg_conninfo(dbname))
    yield pool
    pool.close()


@pytest.fixture
def product_pool(pg_settings):
    """建临时库 + pool + init_product_schema(vector ext + revisions),供策略/画像 store 测试。"""
    dbname = pg_settings.product_db_name(f"prod-{uuid.uuid4().hex[:8]}")
    _create_db(dbname)
    from ksc_mem0_server.runtime.schema import init_product_schema
    from ksc_mem0_server.storage import create_pool

    pool = create_pool(pg_settings.pg_conninfo(dbname))
    init_product_schema(pool)
    yield pool
    pool.close()


@pytest.fixture
def mock_mem0_factories(monkeypatch):
    """mock mem0 的 LLM/embedder/vector 工厂与 telemetry,避免真 OpenAI 调用与网络。

    FactsMemory 构造仍走 super().__init__(Memory),但 embedding/llm/vector_store 被替换为
    MagicMock;self.db 仍被替换为真 PostgreSQLHistoryManager(连真 PG pool)。
    """
    from unittest.mock import MagicMock

    import mem0.memory.main as main
    import mem0.utils.factory as factory

    monkeypatch.setattr(main, "MEM0_TELEMETRY", False)
    monkeypatch.setattr(main, "capture_event", lambda *a, **k: None)
    monkeypatch.setattr(main, "display_first_run_notice", lambda *a, **k: None)
    monkeypatch.setattr(factory.EmbedderFactory, "create", lambda *a, **k: MagicMock())
    monkeypatch.setattr(factory.VectorStoreFactory, "create", lambda *a, **k: MagicMock())
    monkeypatch.setattr(factory.LlmFactory, "create", lambda *a, **k: MagicMock())
    monkeypatch.setattr(factory.VectorStoreFactory, "reset", lambda vs: vs)
