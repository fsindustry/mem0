"""异步任务子系统:进程内内存队列 + 多 product 路由消费 + serialize_key FIFO。"""

from .consumer import TaskConsumer
from .handlers import TaskHandler, clear_handlers, get_handler, register_handler
from .queue import GLOBAL_TASK_QUEUE, GlobalTaskQueue, TaskItem

__all__ = [
    "TaskConsumer",
    "TaskHandler",
    "clear_handlers",
    "get_handler",
    "register_handler",
    "GLOBAL_TASK_QUEUE",
    "GlobalTaskQueue",
    "TaskItem",
]
