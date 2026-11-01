"""Alembic env:应用库 mem0_app 的 schema 迁移。

应用库含 products/api_keys/settings 表(见 ksc_mem0_server/models.py)。
per-product 库由 runtime/schema.py 运行时按需建库建表,不进 Alembic。
URL 从环境变量构建(与 db.py 一致),覆盖 alembic.ini 的静态值。
"""

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

config = context.config

# 从环境变量构建应用库 URL(与 ksc_mem0_server/db.py 一致)
_host = os.environ.get("POSTGRES_HOST", "localhost")
_port = os.environ.get("POSTGRES_PORT", "5433")
_user = os.environ.get("POSTGRES_USER", "postgres")
_password = os.environ.get("POSTGRES_PASSWORD", "postgres")
_app_db = os.environ.get("APP_DB_NAME", "mem0_app")
config.set_main_option("sqlalchemy.url", f"postgresql+psycopg://{_user}:{_password}@{_host}:{_port}/{_app_db}")

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 导入 ORM 模型以注册到 Base.metadata
from ksc_mem0_server.db import Base  # noqa: E402
from ksc_mem0_server import models  # noqa: E402,F401

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
