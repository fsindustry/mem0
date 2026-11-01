"""ProfileEnqueueGate:入队去重(in-flight 集合),handle finally mark_done 清理。

无超时自愈,必须显式 mark_done,否则 (user_id, strategy_id) 卡死无法再入队。
多 product:由 ProductRuntime 持有独立 gate。
"""

import logging
import threading
from typing import Set

logger = logging.getLogger(__name__)


class ProfileEnqueueGate:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._inflight: Set[tuple] = set()

    def try_enqueue(self, user_id: str, strategy_id: str) -> bool:
        """未在途则标记入队返回 True;已在途返回 False(去重)。"""
        key = (user_id, strategy_id)
        with self._lock:
            if key in self._inflight:
                return False
            self._inflight.add(key)
            return True

    def mark_done(self, user_id: str, strategy_id: str) -> None:
        with self._lock:
            self._inflight.discard((user_id, strategy_id))

    def is_inflight(self, user_id: str, strategy_id: str) -> bool:
        with self._lock:
            return (user_id, strategy_id) in self._inflight

    def clear(self) -> None:
        with self._lock:
            self._inflight.clear()
