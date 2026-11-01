"""共享空间抽取任务触发(双路径共用幂等入口)+ interval 兜底调度。

迁移自参考库 shared_space/scheduler.py,简化:interval 代替 croniter(无外部依赖)。
触发双路径(共用 trigger 入口):
  - 登记阈值:fact 落库后登记 pending_tokens 达阈值即时触发
  - cron 兜底(本线程):interval 周期巡检启用空间,对有积压(pending_tokens>0)的空间触发
单空间互斥由 InFlightGate(入队前 acquire,handler finally release)+ run_lease(防多节点并发)。
"""

import logging
import threading
import uuid
from typing import Callable

from ..tasks.queue import TaskItem

logger = logging.getLogger(__name__)

SHARED_SPACE_CATEGORY = "shared_space"
SHARED_SPACE_TASK_TYPE = "extract"

DEFAULT_RUN_LEASE_TTL = 300


class InFlightGate:
    """单空间任务互斥:入队前 acquire,handler finally release。进程内(防同进程并发入队)。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._inflight: set[str] = set()

    def try_acquire(self, space_id: str) -> bool:
        with self._lock:
            if space_id in self._inflight:
                return False
            self._inflight.add(space_id)
            return True

    def release(self, space_id: str) -> None:
        with self._lock:
            self._inflight.discard(space_id)

    def is_inflight(self, space_id: str) -> bool:
        with self._lock:
            return space_id in self._inflight


def make_trigger(
    queue_provider: Callable,
    gate: InFlightGate,
    store,
    product_id: str,
    run_lease_ttl: int = DEFAULT_RUN_LEASE_TTL,
) -> Callable:
    """构造幂等触发入口(登记器与 cron 兜底共用)。

    gate 互斥(进程内)+ run_lease(跨节点)+ 入队 TaskItem(category=shared_space, task_type=extract,
    product_id, user_id=space_id, payload={space_id})。
    """

    def trigger(space_id: str, source: str = "threshold") -> bool:
        if not gate.try_acquire(space_id):
            logger.debug("Shared space trigger gate busy space=%s source=%s", space_id, source)
            return False
        token = str(uuid.uuid4())
        try:
            if not store.try_acquire_run_lease(space_id, token, run_lease_ttl):
                logger.debug("Shared space run_lease busy space=%s", space_id)
                return False
        except Exception:
            gate.release(space_id)
            logger.exception("Shared space run_lease failed space=%s", space_id)
            return False
        item = TaskItem(
            category=SHARED_SPACE_CATEGORY,
            task_type=SHARED_SPACE_TASK_TYPE,
            product_id=product_id,
            user_id=space_id,
            payload={"space_id": space_id, "lease_token": token},
        )
        ok = queue_provider().put(item)
        if not ok:
            store.release_run_lease(space_id, token)
            gate.release(space_id)
            logger.warning("Shared space enqueue failed space=%s", space_id)
            return False
        logger.info("Shared space enqueued space=%s source=%s", space_id, source)
        return True

    trigger._shared_space_source_aware = True  # type: ignore[attr-defined]
    return trigger


def invoke_trigger(trigger: Callable, space_id: str, source: str) -> bool:
    """调用 trigger 传来源;兼容单参数 callable。"""
    if getattr(trigger, "_shared_space_source_aware", False):
        return bool(trigger(space_id, source=source))
    return bool(trigger(space_id))


class SharedSpacePublisher:
    """interval 兜底调度:周期巡检启用空间,对有积压的空间触发抽取。"""

    def __init__(self, store, trigger: Callable, interval: float = 60.0) -> None:
        self._store = store
        self._trigger = trigger
        self._interval = interval

    def publish_round(self) -> int:
        enqueued = 0
        for space in self._store.list_enabled_spaces():
            if (space.get("pending_tokens") or 0) > 0:
                if invoke_trigger(self._trigger, space["space_id"], "cron"):
                    enqueued += 1
        return enqueued

    def run_forever(self, stop_event: threading.Event) -> None:
        while not stop_event.wait(self._interval):
            try:
                n = self.publish_round()
                if n:
                    logger.info("SharedSpacePublisher enqueued %d spaces", n)
            except Exception:
                logger.exception("SharedSpacePublisher round failed")
