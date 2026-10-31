"""跨 Uvicorn worker 的策略版本轮询器。

每个 product 一个 watcher 线程轮询 DB 版本,原子刷新本进程策略快照。
P1a 默认用惰性 check_and_reload(add 前调用)而非启动 watcher 线程,
避免 N product × 线程膨胀;watcher 保留供需要主动热刷新的场景。
"""

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class StrategyWatcher:
    def __init__(self, store, stop_event: threading.Event, check_interval: float = 3.0) -> None:
        self._store = store
        self._global_stop = stop_event
        self._stop_event = threading.Event()
        self._check_interval = max(1.0, float(check_interval))
        self._thread: Optional[threading.Thread] = None
        self._last_success_at: float = 0.0
        self._last_error: Optional[str] = None

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="strategy-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _stopped(self) -> bool:
        return self._stop_event.is_set() or self._global_stop.is_set()

    def _run(self) -> None:
        while not self._stopped():
            try:
                self._store.check_and_reload()
                self._last_success_at = time.time()
                self._last_error = None
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("Strategy watcher poll failed; keeping previous snapshot")
            if self._stop_event.wait(self._check_interval) or self._global_stop.is_set():
                break
        logger.info("StrategyWatcher stopped")

    def get_status(self) -> Dict[str, Any]:
        return {
            "alive": self.is_alive(),
            "check_interval_seconds": self._check_interval,
            "last_success_at": (
                datetime.fromtimestamp(self._last_success_at, timezone.utc).isoformat()
                if self._last_success_at > 0
                else None
            ),
            "last_error": self._last_error,
        }
