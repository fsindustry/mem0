"""全局内存任务队列(多 product 共用,TaskItem 带 product_id)。

不落盘,服务重启后队列中未消费任务允许丢失;可靠性靠各子系统的 events/failures
表 next_check_at 扫描补偿(scanner worker,P1b-3 接入)。

迁移自参考库 release-v2.2 tasks/queue.py,改造:TaskItem 增加 product_id 字段
(多租户路由);consumer 据此取对应 ProductRuntime。
"""

import logging
import queue
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TaskItem:
    """队列中的任务条目。

    Fields:
        category: 任务类别(如 "profile")。
        task_type: 类别内子类型(如 "review")。
        product_id: 归属 product(多租户路由 key)。
        user_id: 任务归属用户。
        payload: 任务上下文,由发布器构建、消费者解读。
        created_at: 入队时间戳(自动)。
        task_id: 唯一标识(自动 uuid4)。
        serialize_key: 同 key 严格 FIFO 串行分发键。空串=并发路径;非空=同 key
            前一个执行完才 submit 下一个,跨 key 仍并发。
    """

    category: str
    task_type: str
    product_id: str
    user_id: str
    payload: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    task_id: str = ""
    serialize_key: str = ""

    def __post_init__(self) -> None:
        if not self.task_id:
            self.task_id = str(uuid.uuid4())


class GlobalTaskQueue:
    """进程内单例内存队列,多类别多 product 共用。不落盘,重启丢失。"""

    DEFAULT_MAXSIZE = 1000

    def __init__(self, maxsize: int = DEFAULT_MAXSIZE) -> None:
        self._q: "queue.Queue[TaskItem]" = queue.Queue(maxsize=maxsize)

    def put(self, item: TaskItem, timeout: Optional[float] = None) -> bool:
        """入队。timeout=None 非阻塞;指定 timeout 时队列满则等待,超时丢弃。"""
        try:
            self._q.put(item, block=timeout is not None, timeout=timeout)
            return True
        except queue.Full:
            logger.warning(
                "Task queue full, dropped category=%s product=%s user=%s",
                item.category,
                item.product_id,
                item.user_id,
            )
            return False

    def get(self, block: bool = True, timeout: Optional[float] = None) -> Optional[TaskItem]:
        try:
            return self._q.get(block=block, timeout=timeout)
        except queue.Empty:
            return None

    def task_done(self) -> None:
        self._q.task_done()

    def qsize(self) -> int:
        return self._q.qsize()


GLOBAL_TASK_QUEUE = GlobalTaskQueue()
