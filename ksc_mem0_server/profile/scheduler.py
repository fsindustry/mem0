"""ProfilePublisher:周期扫描 due 事件 + pending buffer,入队画像 review 任务。

简化版:扫 task_store.due()(已就绪待处理)直接入队,不依赖 croniter。
多 product:由 ProductRuntime 持有;全局 worker 周期遍历各 product 的 publisher。
"""

import logging
import threading

logger = logging.getLogger(__name__)


class ProfilePublisher:
    def __init__(self, task_store, enqueue_gate, queue, product_id: str, interval: float = 60.0) -> None:
        self._tasks = task_store
        self._gate = enqueue_gate
        self._queue = queue
        self.product_id = product_id
        self._interval = interval

    def publish_round(self) -> int:
        """扫 due 事件,对每个 (user, strategy) 经 gate 去重后入队。返回入队数。"""
        due = self._tasks.due(limit=100)
        enqueued = 0
        for d in due:
            user_id = d["user_id"]
            strategy_id = d["strategy_id"]
            if not self._gate.try_enqueue(user_id, strategy_id):
                continue
            from ..tasks.queue import TaskItem

            item = TaskItem(
                category="profile",
                task_type="review",
                product_id=self.product_id,
                user_id=user_id,
                payload={"user_id": user_id, "strategy_id": strategy_id},
            )
            if self._queue.put(item):
                enqueued += 1
            else:
                self._gate.mark_done(user_id, strategy_id)  # 入队失败,回退 gate
        return enqueued

    def run_forever(self, stop_event: threading.Event) -> None:
        while not stop_event.wait(self._interval):
            try:
                n = self.publish_round()
                if n:
                    logger.info("ProfilePublisher enqueued %d tasks product=%s", n, self.product_id)
            except Exception:
                logger.exception("ProfilePublisher round failed product=%s", self.product_id)
