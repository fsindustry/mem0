"""任务消费线程:从 GlobalTaskQueue 取任务,按 product_id 路由到 ProductRuntime,分发到 handler。

serialize_key 严格 FIFO:同 serialize_key 的任务前一个执行完才 submit 下一个(跨 key 仍并发)。
多 product:TaskItem.product_id → registry.get_or_create(product_id) 取 ProductRuntime,
handler.handle(item, runtime) 据此访问该 product 的 stores。
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from .handlers import get_handler
from .queue import GlobalTaskQueue, TaskItem

logger = logging.getLogger(__name__)


class TaskConsumer:
    def __init__(
        self,
        stop_event: threading.Event,
        queue: GlobalTaskQueue,
        registry,
        concurrency: int = 4,
    ) -> None:
        self._stop = stop_event
        self._queue = queue
        self._registry = registry
        self._executor = ThreadPoolExecutor(max_workers=concurrency)
        self._inflight = 0
        self._inflight_lock = threading.Lock()
        self._serial_busy: dict[str, bool] = {}
        self._serial_pending: dict[str, list[TaskItem]] = {}
        self._serial_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="task-consumer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)
        self._executor.shutdown(wait=False)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def is_idle(self) -> bool:
        with self._inflight_lock:
            return self._inflight == 0

    def _run(self) -> None:
        while not self._stop.is_set():
            item = self._queue.get(block=True, timeout=1)
            if item is None:
                continue
            self._dispatch(item)

    def _dispatch(self, item: TaskItem) -> None:
        key = item.serialize_key
        if key:
            with self._serial_lock:
                if self._serial_busy.get(key):
                    self._serial_pending.setdefault(key, []).append(item)
                    return
                self._serial_busy[key] = True
        self._submit(item)

    def _submit(self, item: TaskItem) -> None:
        with self._inflight_lock:
            self._inflight += 1
        self._executor.submit(self._execute, item)

    def _execute(self, item: TaskItem) -> None:
        try:
            handler = get_handler(item.category, item.task_type)
            if handler is None:
                logger.warning("No handler for %s/%s, drop item", item.category, item.task_type)
                return
            runtime = self._registry.get_or_create(item.product_id)
            handler.handle(item, runtime)
        except Exception:
            logger.exception(
                "Task failed category=%s task_type=%s product=%s",
                item.category,
                item.task_type,
                item.product_id,
            )
        finally:
            with self._inflight_lock:
                self._inflight -= 1
            if item.serialize_key:
                self._drain_serial_next(item.serialize_key)
            self._queue.task_done()

    def _drain_serial_next(self, key: str) -> None:
        with self._serial_lock:
            pending = self._serial_pending.get(key)
            if not pending:
                self._serial_busy[key] = False
                return
            nxt = pending.pop(0)
        self._submit(nxt)
