"""SummaryPublisher:周期扫描 summary due 事件,入队摘要 review 任务。"""

import logging

from ..tasks.queue import TaskItem

logger = logging.getLogger(__name__)


class SummaryPublisher:
    def __init__(self, task_store, queue, product_id: str, interval: float = 60.0) -> None:
        self._tasks = task_store
        self._queue = queue
        self.product_id = product_id
        self._interval = interval

    def publish_round(self) -> int:
        due = self._tasks.due(limit=100)
        enqueued = 0
        for d in due:
            item = TaskItem(
                category="summary",
                task_type="review",
                product_id=self.product_id,
                user_id=d["user_id"],
                payload={"user_id": d["user_id"], "run_id": d["run_id"]},
            )
            if self._queue.put(item):
                enqueued += 1
        return enqueued
