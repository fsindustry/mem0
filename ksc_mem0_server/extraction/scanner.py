"""FailureCompensationScanner:全局后台线程,周期遍历各 product 补偿失败任务。

迁移自参考库 extraction/scanner.py,改为多 product:遍历 registry 中所有 ProductRuntime,
对每个 product 的 extraction_failures 表 due 记录 CAS 抢占 → 调 rerun 回调 → mark_succeeded/bump_retry。
reclaim_stale_running 回收超 lease 的 RUNNING。

rerun 回调由调用方注入(根据 strategy_type 路由到 facts/profile/summary 的重跑逻辑)。
"""

import logging
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class FailureCompensationScanner:
    def __init__(
        self,
        stop_event: threading.Event,
        registry,
        rerun: Callable[[Any, dict], bool],
        interval: float = 30.0,
    ) -> None:
        """Args:
            stop_event: 进程关闭事件。
            registry: ProductRegistry,遍历其 runtimes。
            rerun: rerun(runtime, failure_dict) -> bool,True=成功 mark_succeeded,False=失败 bump_retry。
            interval: 扫描间隔(秒)。
        """
        self._stop = stop_event
        self._registry = registry
        self._rerun = rerun
        self._interval = interval
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="extraction-compensation-scanner", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def scan_once(self) -> int:
        """扫一轮所有 product 的失败补偿,返回处理数。"""
        handled = 0
        for product_id, runtime in self._registry.snapshot().items():
            store = runtime.extraction_failure_store
            store.reclaim_stale_running()
            for failure in store.due(limit=100):
                owner = store.mark_running(failure["id"])
                if owner is None:
                    continue
                try:
                    ok = self._rerun(runtime, failure)
                except Exception:
                    logger.exception("rerun failed failure=%s product=%s", failure["id"], product_id)
                    ok = False
                if ok:
                    store.mark_succeeded(failure["id"], owner)
                else:
                    store.bump_retry(failure["id"], owner)
                handled += 1
        return handled

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                n = self.scan_once()
                if n:
                    logger.info("Compensation scanner handled %d failures", n)
            except Exception:
                logger.exception("Compensation scan round failed")
