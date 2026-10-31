"""服务配置:环境变量读取,集中管理连接、模型、鉴权与分库参数。

设计为"env 唯一来源"(P0);后续可叠加 config.yaml 补默认(参考 release-v2.2
ksc-mem0/mem0_server/config_loader.py 的"yaml 补默认、env 优先"模式)。
"""

import os
from dataclasses import dataclass, field


def _env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    """全局服务配置(单例,进程内只读)。"""

    # --- PostgreSQL 实例(承载应用库 + 所有 product 库) ---
    pg_host: str = field(default_factory=lambda: os.environ.get("POSTGRES_HOST", "localhost"))
    pg_port: int = field(default_factory=lambda: _env_int("POSTGRES_PORT", 5432))
    pg_user: str = field(default_factory=lambda: os.environ.get("POSTGRES_USER", "postgres"))
    pg_password: str = field(default_factory=lambda: os.environ.get("POSTGRES_PASSWORD", "postgres"))
    # 应用库(用户/鉴权/产品登记/配置)
    app_db_name: str = field(default_factory=lambda: os.environ.get("APP_DB_NAME", "mem0_app"))
    # product 库命名:前缀 + product_id(其中 '-' -> '_')
    product_db_prefix: str = field(default_factory=lambda: os.environ.get("PRODUCT_DB_PREFIX", "mem0_"))
    # 默认 maintenance 库(用于 CREATE DATABASE,PG 无 IF NOT EXISTS,需先连一个库)
    maintenance_db: str = field(default_factory=lambda: os.environ.get("MAINTENANCE_DB", "postgres"))

    # --- 连接池 ---
    pool_minconn: int = field(default_factory=lambda: _env_int("PG_POOL_MINCONN", 1))
    pool_maxconn: int = field(default_factory=lambda: _env_int("PG_POOL_MAXCONN", 5))

    # --- LLM / Embedder(mem0 provider 配置) ---
    openai_api_key: str | None = field(default_factory=lambda: os.environ.get("OPENAI_API_KEY"))
    llm_provider: str = field(default_factory=lambda: os.environ.get("MEM0_LLM_PROVIDER", "openai"))
    llm_model: str = field(default_factory=lambda: os.environ.get("MEM0_LLM_MODEL", "gpt-4.1-nano-2025-04-14"))
    llm_temperature: float = field(default_factory=lambda: float(os.environ.get("MEM0_LLM_TEMPERATURE", "0.2")))
    embedder_provider: str = field(default_factory=lambda: os.environ.get("MEM0_EMBEDDER_PROVIDER", "openai"))
    embedder_model: str = field(default_factory=lambda: os.environ.get("MEM0_EMBEDDER_MODEL", "text-embedding-3-small"))
    embedding_dims: int = field(default_factory=lambda: _env_int("EMBEDDING_DIMS", 1536))

    # --- 向量 collection 命名(每库内固定 6 张 pgvector 表) ---
    collection_facts: str = field(default_factory=lambda: os.environ.get("COLLECTION_FACTS", "mem0"))
    collection_profile: str = field(default_factory=lambda: os.environ.get("COLLECTION_PROFILE", "mem0_profile"))
    collection_summary: str = field(default_factory=lambda: os.environ.get("COLLECTION_SUMMARY", "mem0_summary"))
    collection_membox: str = field(default_factory=lambda: os.environ.get("COLLECTION_MEMBOX", "mem0_membox"))
    collection_memscene: str = field(default_factory=lambda: os.environ.get("COLLECTION_MEMSCENE", "mem0_memscene"))
    collection_shared: str = field(default_factory=lambda: os.environ.get("COLLECTION_SHARED", "mem0_shared"))

    # --- 鉴权 ---
    auth_disabled: bool = field(default_factory=lambda: _env_bool("AUTH_DISABLED", False))
    admin_api_key: str | None = field(default_factory=lambda: os.environ.get("ADMIN_API_KEY"))

    # --- 服务 ---
    host: str = field(default_factory=lambda: os.environ.get("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _env_int("PORT", 8000))

    def pg_conninfo(self, dbname: str, *, sslmode: str | None = None) -> str:
        """构造 psycopg3 conninfo 字符串。"""
        conninfo = f"postgresql://{self.pg_user}:{self.pg_password}@{self.pg_host}:{self.pg_port}/{dbname}"
        if sslmode:
            conninfo += f"?sslmode={sslmode}"
        return conninfo

    def product_db_name(self, product_id: str) -> str:
        """product_id -> 库名:前缀 + product_id 中 '-' 转为 '_'。"""
        normalized = product_id.replace("-", "_")
        return f"{self.product_db_prefix}{normalized}"


_settings: Settings | None = None


def get_settings() -> Settings:
    """全局 Settings 单例(惰性,便于测试 monkeypatch 环境变量后重建)。"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """测试用:清空缓存,使下次 get_settings() 重新读取环境变量。"""
    global _settings
    _settings = None
