"""应用库访问层(SQLAlchemy)。

应用库(默认 mem0_app)存放跨 product 的共享数据:products 登记、API key、settings。
与 per-product 库分离:后者由 psycopg3 ConnectionPool 直连(见 storage/pg_pool.py)。
"""

import logging

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

logger = logging.getLogger(__name__)


def _app_db_url() -> str:
    s = get_settings()
    return f"postgresql+psycopg://{s.pg_user}:{s.pg_password}@{s.pg_host}:{s.pg_port}/{s.app_db_name}"


def _app_conninfo() -> str:
    s = get_settings()
    return s.pg_conninfo(s.app_db_name)


def ensure_app_database() -> None:
    """确保应用库 database 存在:连 maintenance 库,不存在则 CREATE DATABASE。

    PostgreSQL 无 `CREATE DATABASE IF NOT EXISTS`,需先查 pg_database 再创建;
    且 CREATE DATABASE 不能在事务块内执行,故用 autocommit 连接。
    """
    import psycopg

    s = get_settings()
    with psycopg.connect(s.pg_conninfo(s.maintenance_db), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (s.app_db_name,))
            if cur.fetchone() is None:
                cur.execute(f'CREATE DATABASE "{s.app_db_name}"')
                logger.info("Created app database: %s", s.app_db_name)


# 模块级 engine / session(对齐官方 server/db.py;测试设好 env 后 import 本模块)
engine = create_engine(_app_db_url(), pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI 依赖:yield 一个 SQLAlchemy session。"""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_app_db() -> None:
    """建应用库 + 表(开发期用 create_all;生产用 alembic,见 P5)。"""
    ensure_app_database()
    from . import models  # noqa: F401  注册 ORM 模型到 Base.metadata

    Base.metadata.create_all(engine)
    logger.info("App DB initialized: %s", get_settings().app_db_name)
