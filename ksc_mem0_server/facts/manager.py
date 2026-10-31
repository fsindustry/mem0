"""FactsMemory:mem0 Memory 的最小子类,将历史存储从 SQLite 换为 PostgreSQL。

设计原则(对齐"保留 mem0 最新版本代码底座"):
  - 不改动 mem0/memory/main.py 任何一行;继承 Memory,仅 override 三个触碰 self.db 的方法。
  - vector_store / embedding_model / llm / entity_store / pipeline 全部沿用 mem0 原生:
      vector_store 由 mem0 用 MemoryConfig.vector_store 创建(pgvector,连该 product 库),
      这正是"按 product_id 分库"的落地——每个 product 的 FactsMemory 连自己的 PG 库。
  - 唯一替换:self.db(SQLiteManager) -> PostgreSQLHistoryManager(接口/返回格式对齐)。

config.history_db_path 设为 ":memory:",使 super().__init__ 创建的 SQLiteManager
落在内存(随即被替换),避免在每个 product 库旁遗留无用 sqlite 文件。
"""

import logging

from mem0.configs.base import MemoryConfig
from mem0.memory.main import Memory
from mem0.utils.factory import VectorStoreFactory

from ..storage.pg_history import PostgreSQLHistoryManager

logger = logging.getLogger(__name__)


class FactsMemory(Memory):
    """事实记忆:mem0 Memory + PostgreSQL 历史。"""

    def __init__(self, config: MemoryConfig, history_pool) -> None:
        super().__init__(config)
        # 替换 SQLite 历史为 PostgreSQL 历史(连该 product 库的共享 pool)
        self._history_pool = history_pool
        self.db = PostgreSQLHistoryManager(history_pool)

    def reset(self) -> None:
        """复刻 mem0 Memory.reset,仅把 SQLite 重建换为 PostgreSQL 重建。"""
        logger.warning("Resetting all memories (product facts)")

        self.db.reset()
        self.db.close()
        self.db = PostgreSQLHistoryManager(self._history_pool)

        if hasattr(self.vector_store, "reset"):
            self.vector_store = VectorStoreFactory.reset(self.vector_store)
        else:
            logger.warning("Vector store does not support reset. Skipping.")
            self.vector_store.delete_col()
            self.vector_store = VectorStoreFactory.create(
                self.config.vector_store.provider, self.config.vector_store.config
            )
        if self._entity_store is not None:
            try:
                self._entity_store.reset()
            except Exception as e:
                logger.warning(f"Failed to reset entity store: {e}")
            self._entity_store = None

    def close(self) -> None:
        """释放资源:history manager(no-op,不关共享 pool)。vector_store 由 mem0 自管。"""
        if hasattr(self, "db") and self.db is not None:
            self.db.close()
            self.db = None
