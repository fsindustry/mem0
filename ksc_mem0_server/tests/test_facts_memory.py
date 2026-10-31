"""FactsMemory 测试:验证 self.db 被替换为 PostgreSQLHistoryManager,reset/close 行为正确。

mock mem0 工厂(避免真 OpenAI);history 走真 PG pool。
"""

import uuid

from mem0.configs.base import MemoryConfig

from ksc_mem0_server.facts import FactsMemory
from ksc_mem0_server.storage.pg_history import PostgreSQLHistoryManager


def _config(settings, db_name: str) -> MemoryConfig:
    return MemoryConfig(
        **{
            "version": "v1.1",
            "vector_store": {
                "provider": "pgvector",
                "config": {
                    "host": settings.pg_host,
                    "port": settings.pg_port,
                    "dbname": db_name,
                    "user": settings.pg_user,
                    "password": settings.pg_password,
                    "collection_name": settings.collection_facts,
                    "embedding_model_dims": settings.embedding_dims,
                },
            },
            "llm": {"provider": settings.llm_provider, "config": {"api_key": settings.openai_api_key, "model": settings.llm_model}},
            "embedder": {"provider": settings.embedder_provider, "config": {"api_key": settings.openai_api_key, "model": settings.embedder_model}},
            "history_db_path": ":memory:",
        }
    )


def test_facts_memory_uses_pg_history(pg_settings, mock_mem0_factories, history_pool):
    db_name = pg_settings.product_db_name(f"facts-{uuid.uuid4().hex[:8]}")
    mem = FactsMemory(_config(pg_settings, db_name), history_pool)
    assert isinstance(mem.db, PostgreSQLHistoryManager)
    assert mem.db.pool is history_pool
    mem.close()
    assert mem.db is None


def test_facts_memory_reset_rebuilds_pg_history(pg_settings, mock_mem0_factories, history_pool):
    db_name = pg_settings.product_db_name(f"facts-{uuid.uuid4().hex[:8]}")
    mem = FactsMemory(_config(pg_settings, db_name), history_pool)
    mem.db.add_history("m1", None, "x", "ADD")
    assert len(mem.db.get_history("m1")) == 1
    mem.reset()
    assert isinstance(mem.db, PostgreSQLHistoryManager)
    assert mem.db.get_history("m1") == []
    mem.close()
