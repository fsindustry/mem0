"""线程守护:监控 daemon 工作线程(consumer/publisher),崩溃重启。

P1b-1 提供基础重启能力;指数退避/存活时长判定等精细化策略后续完善。
"""

import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class ThreadWatchdog:
    def __init__(self, stop_event: threading.Event, check_interval: float = 5.0) -> None:
        self._stop = stop_event
        self._check_interval = check_interval
        self._workers: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def register(self, name: str, factory: Callable[[], threading.Thread]) -> None:
        """注册一个工作线程工厂(factory 每次调用返回一个新 daemon 线程,未 start)。"""
        with self._lock:
            self._workers[name] = {"factory": factory, "thread": None, "alive_since": 0.0}

    def start_all(self) -> None:
        with self._lock:
            for name, w in self._workers.items():
                t = w["factory"]()
                t.start()
                w["thread"] = t
                w["alive_since"] = time.time()
        self._thread = threading.Thread(target=self._supervise, name="watchdog", daemon=True)
        self._thread.start()

    def _supervise(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                for name, w in list(self._workers.items()):
                    t = w["thread"]
                    if t is None or not t.is_alive():
                        try:
                            t2 = w["factory"]()
                            t2.start()
                            w["thread"] = t2
                            w["alive_since"] = time.time()
                            logger.warning("Watchdog restarted worker: %s", name)
                        except Exception:
                            logger.exception("Watchdog failed to restart worker: %s", name)
            if self._stop.wait(self._check_interval):
                break

    def stop(self) -> None:
        self._stop.set()

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
