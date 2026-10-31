"""ProductRuntime:单个 product 的运行时,持有该 product 库的连接池与各子系统。

P0 仅持有 facts(FactsMemory);P1+ 追加 profile/summary/digests/shared_space/strategy/
tasks/extraction 子系统,均复用 self.pool(共享连接池)。

按 product_id 分库的落地:每个 ProductRuntime 连自己的 PG 库(mem0_{pid_normalized}),
FactsMemory 的 vector_store 由 mem0 用 MemoryConfig 创建(pgvector 指向该库)。
"""

import logging

from mem0.configs.base import MemoryConfig

from ..config import get_settings
from ..facts import FactsMemory
from ..storage import create_pool
from .schema import init_product_schema

logger = logging.getLogger(__name__)


class ProductRuntime:
    """单 product 运行时:连接池 + 事实记忆(+ 后续子系统)。"""

    def __init__(self, product_id: str, config_overrides: dict | None = None) -> None:
        s = get_settings()
        self.product_id = product_id
        self.db_name = s.product_db_name(product_id)
        self._config_overrides = config_overrides or {}

        # 该 product 库的共享连接池(history + P1+ 各 store 共用;pgvector 由 mem0 自建)
        self.pool = create_pool(
            s.pg_conninfo(self.db_name), min_size=s.pool_minconn, max_size=s.pool_maxconn
        )
        init_product_schema(self.pool)

        self.facts = self._build_facts()
        logger.info("ProductRuntime ready: %s -> db %s", product_id, self.db_name)

    def _build_facts(self) -> FactsMemory:
        s = get_settings()
        cfg = {
            "version": "v1.1",
            "vector_store": {
                "provider": "pgvector",
                "config": {
                    "host": s.pg_host,
                    "port": s.pg_port,
                    "dbname": self.db_name,
                    "user": s.pg_user,
                    "password": s.pg_password,
                    "collection_name": s.collection_facts,
                    "embedding_model_dims": s.embedding_dims,
                },
            },
            "llm": {
                "provider": s.llm_provider,
                "config": {"api_key": s.openai_api_key, "temperature": s.llm_temperature, "model": s.llm_model},
            },
            "embedder": {
                "provider": s.embedder_provider,
                "config": {"api_key": s.openai_api_key, "model": s.embedder_model},
            },
            # mem0 super().__init__ 会建一个 SQLiteManager;设 :memory: 使其落在内存,
            # 随即被 FactsMemory 替换为 PostgreSQLHistoryManager,不留无用文件。
            "history_db_path": ":memory:",
        }
        custom_instructions = self._config_overrides.get("custom_instructions")
        if custom_instructions:
            cfg["custom_instructions"] = custom_instructions
        config = MemoryConfig(**cfg)
        return FactsMemory(config, self.pool)

    def close(self) -> None:
        try:
            if self.facts:
                self.facts.close()
        finally:
            self.pool.close()
            logger.info("ProductRuntime closed: %s", self.product_id)
