"""ProductRuntime:单个 product 的运行时,持有该 product 库的连接池与各子系统。

P0 仅持有 facts(FactsMemory);P1+ 追加 profile/summary/digests/shared_space/strategy/
tasks/extraction 子系统,均复用 self.pool(共享连接池)。

按 product_id 分库的落地:每个 ProductRuntime 连自己的 PG 库(mem0_{pid_normalized}),
FactsMemory 的 vector_store 由 mem0 用 MemoryConfig 创建(pgvector 指向该库)。
"""

import logging
from typing import Optional

from mem0.configs.base import MemoryConfig

from ..config import get_settings
from ..facts import FactsMemory
from ..profile import (
    ProfileEnqueueGate,
    ProfilePendingBuffer,
    ProfilePublisher,
    ProfileReviewer,
    ProfileRuntime,
    ProfileStore,
    ProfileStrategyStore,
    ProfileTaskStore,
    ensure_profile_schema,
    register_profile_handler,
)
from ..storage import create_pool
from ..strategy import StrategyStore
from .schema import init_product_schema

logger = logging.getLogger(__name__)


def _get_global_queue():
    from ..tasks.queue import GLOBAL_TASK_QUEUE

    return GLOBAL_TASK_QUEUE


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

        self.strategy_store = StrategyStore(self.pool, product_id)
        ensure_profile_schema(self.pool)
        self.profile_store = ProfileStore(self.pool, dims=s.embedding_dims)
        self.profile_strategy_store = ProfileStrategyStore(self.pool, product_id, self.strategy_store)
        self.profile_task_store = ProfileTaskStore(self.pool, product_id)
        self.facts = self._build_facts()
        self._build_profile_runtime()
        logger.info("ProductRuntime ready: %s -> db %s", product_id, self.db_name)

    def _build_profile_runtime(self) -> None:
        """实例化画像生成闭环 + 注册 handler。LLM/embedder 复用 facts 的 mem0 实例。"""
        reviewer = ProfileReviewer(
            llm=self.facts.llm,
            embedding_model=self.facts.embedding_model,
            profile_store=self.profile_store,
            strategy_store=self.profile_strategy_store,
        )
        self.profile_runtime = ProfileRuntime(
            self.profile_task_store, self.profile_store, self.strategy_store, reviewer
        )
        self.profile_gate = ProfileEnqueueGate()
        self.profile_buffer = ProfilePendingBuffer()
        self.profile_publisher = ProfilePublisher(
            self.profile_task_store, self.profile_gate, _get_global_queue(), self.product_id
        )
        register_profile_handler(self.profile_runtime, self.profile_gate, self.profile_buffer)

    def trigger_profile(self, user_id: str, strategy_id: str, inputs: list) -> str:
        """事实记忆 add 后触发画像:登记就绪事件 + 标记 buffer(供 publisher 入队)。"""
        self.profile_buffer.mark_new(user_id)
        return self.profile_runtime.trigger(user_id, strategy_id, inputs)

    def get_fact_prompt(self) -> Optional[str]:
        """事实记忆 add 的 custom_instructions:global 策略合并 prompt(惰性刷新)。

        每次 add 前惰性 check_and_reload(轻量 SELECT revision),取 global 策略
        合并 prompt 注入 mem0 add 的 custom_instructions 插槽;无 global 策略返回 None。
        """
        self.strategy_store.check_and_reload()
        merged = self.strategy_store.get_merged_prompt()
        return merged or None

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
