"""MemBoxPendingQueue:membox seal 落库后 push,供 overall handler drain 合并到 memscene。

按 (box_id, owner_type) 去重,同 owner 重复 push 幂等跳过。
多 product:由 ProductRuntime 持有独立队列。
"""

import logging
import threading
from collections import defaultdict
from typing import Dict, List, Set, Tuple

logger = logging.getLogger(__name__)


class MemBoxPendingQueue:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_owner: Dict[Tuple[str, str], Set[str]] = defaultdict(set)

    def push(self, box_id: str, owner_type: str, owner_id: str) -> bool:
        """push 一条 (box_id, owner)。去重返回 True,已存在返回 False。"""
        key = (owner_type, owner_id)
        with self._lock:
            if box_id in self._by_owner[key]:
                return False
            self._by_owner[key].add(box_id)
            return True

    def drain(self, owner_type: str, owner_id: str) -> List[str]:
        """取出该 owner 全部待处理 box_id 并清空。"""
        key = (owner_type, owner_id)
        with self._lock:
            ids = list(self._by_owner.pop(key, set()))
        return ids

    def has_pending(self, owner_type: str, owner_id: str) -> bool:
        with self._lock:
            return bool(self._by_owner.get((owner_type, owner_id)))

    def pending_owners(self) -> List[Tuple[str, str]]:
        with self._lock:
            return list(self._by_owner.keys())

    def clear(self) -> None:
        with self._lock:
            self._by_owner.clear()
