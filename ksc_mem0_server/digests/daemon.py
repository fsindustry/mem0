"""digests daemon:interval 周期触发 overall aggregate(扫 pending_queue pending_owners 入队)。

迁移自参考库 digests/scheduler.py DigestDailyPublisher,简化:
  - 用 interval 而非 croniter(无外部依赖)
  - 不做主记忆 sweeper 切段(membox seal 由 API 直接触发,见 P3d API)
  - 扫 pending_queue.pending_owners → 入队 overall aggregate 任务
多 product:由 ProductRuntime 持有;全局 worker 周期遍历。
"""

import logging
import threading

from ..tasks.queue import GLOBAL_TASK_QUEUE, TaskItem
from .overall_handler import OVERALL_CATEGORY, OVERALL_TASK_TYPE

logger = logging.getLogger(__name__)


class DigestsPublisher:
    def __init__(self, pending_queue, product_id: str, interval: float = 60.0) -> None:
        self._pending_queue = pending_queue
        self.product_id = product_id
        self._interval = interval

    def publish_round(self) -> int:
        """扫 pending_queue 待处理 owners → 入队 overall aggregate。返回入队数。"""
        owners = self._pending_queue.pending_owners()
        enqueued = 0
        for owner_type, owner_id in owners:
            # 检查仍有 pending(drain 在 aggregate 时发生,此处只入队触发)
            if not self._pending_queue.has_pending(owner_type, owner_id):
                continue
            item = TaskItem(
                category=OVERALL_CATEGORY,
                task_type=OVERALL_TASK_TYPE,
                product_id=self.product_id,
                user_id=owner_id,
                payload={"owner_type": owner_type, "owner_id": owner_id},
            )
            if GLOBAL_TASK_QUEUE.put(item):
                enqueued += 1
        return enqueued

    def run_forever(self, stop_event: threading.Event) -> None:
        while not stop_event.wait(self._interval):
            try:
                n = self.publish_round()
                if n:
                    logger.info("DigestsPublisher enqueued %d overall tasks product=%s", n, self.product_id)
            except Exception:
                logger.exception("DigestsPublisher round failed product=%s", self.product_id)
