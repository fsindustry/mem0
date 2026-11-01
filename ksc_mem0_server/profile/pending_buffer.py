"""ProfilePendingBuffer:per-user 内存双缓冲,标记有新消息的用户。

after_turn mark_new → cron snapshot_current 扫表入队 → handler finally finish_review 交换。
多 product:由 ProductRuntime 持有独立 buffer(无需 product_id key)。
迁移自参考库 profile/pending_buffer.py,简化为 per-user 集合。
"""

import logging
import threading
from typing import Set

logger = logging.getLogger(__name__)


class ProfilePendingBuffer:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: Set[str] = set()  # 本轮待画像用户
        self._next: Set[str] = set()  # 期间新到达用户(下轮)

    def mark_new(self, user_id: str) -> None:
        with self._lock:
            self._current.add(user_id)

    def snapshot_current(self) -> list:
        """取出本轮用户,期间新到达攒到 next,返回 current 并清空 current。"""
        with self._lock:
            users = list(self._current)
            self._current.clear()
            return users

    def finish_review(self, user_id: str) -> None:
        """review 完成:把 next 中该用户迁回 current(若在 next),否则忽略。

        语义:review 期间该用户新到达的消息(next)不应丢,迁回 current 下轮再处理;
        若无新消息则不重新入队(避免空转)。
        """
        with self._lock:
            if user_id in self._next:
                self._next.discard(user_id)
                self._current.add(user_id)

    def has_new(self, user_id: str) -> bool:
        with self._lock:
            return user_id in self._current or user_id in self._next

    def clear(self) -> None:
        with self._lock:
            self._current.clear()
            self._next.clear()
