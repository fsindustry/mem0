"""运行时:ProductRuntime(单 product 的连接池+各子系统)与 ProductRegistry(多 product 路由)。"""

from .product import ProductRuntime
from .registry import ProductRegistry, get_registry, reset_registry

__all__ = ["ProductRuntime", "ProductRegistry", "get_registry", "reset_registry"]
