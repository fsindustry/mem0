"""事实记忆子系统:基于 mem0 官方 Memory,仅替换历史存储为 PostgreSQL。"""

from .manager import FactsMemory

__all__ = ["FactsMemory"]
