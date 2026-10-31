"""任务 handler 注册表:(category, task_type) → handler。

多租户:handler.handle(item, runtime) 接收按 product_id 路由后的 ProductRuntime,
从中取该 product 的 stores。handler 本身无 product 状态,可全局注册单实例。
"""

from typing import Any, Optional, Protocol


class TaskHandler(Protocol):
    def handle(self, item: Any, runtime: Any) -> None: ...


_HANDLERS: dict[tuple[str, str], TaskHandler] = {}


def register_handler(category: str, task_type: str, handler: TaskHandler) -> None:
    _HANDLERS[(category, task_type)] = handler


def get_handler(category: str, task_type: str) -> Optional[TaskHandler]:
    return _HANDLERS.get((category, task_type))


def clear_handlers() -> None:
    """测试用:清空注册表。"""
    _HANDLERS.clear()
