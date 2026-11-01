"""MemBoxSealHandler:会话内话题密封(Pextract → compose → embed + upsert)。

迁移自参考库 digests/membox_handler.py,简化:
  - 不依赖 sweeper 切段/episode 回写(主记忆 episode 结构复杂,留 P5 完善);
    直接接受消息列表 BoxSnapshot,seal 后落 membox。
  - 保留核心:Pextract 提取 {topic,keywords,events}→ compose content → embed+upsert membox → push pending_queue。
  - 合并前置(找同话题 C 合并)留 P3c overall 聚合时处理,seal 阶段独立落库。
"""

import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..profile.review import call_with_parse_retry
from .constants import now_iso
from .pending_queue import MemBoxPendingQueue
from .prompts import build_membox_pextract_prompt, parse_pextract_result

logger = logging.getLogger(__name__)

MEMBOX_CATEGORY = "digests"
MEMBOX_TASK_TYPE = "seal"
_MEMBOX_PEXTRACT_RETRIES = 2


@dataclass
class BoxSnapshot:
    """未 seal 的话题快照(sweeper 切段产出,P3b 由调用方直接构造)。"""

    owner_type: str  # "user" / "agent"
    owner_id: str
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    run_id: Optional[str] = None
    messages: List[str] = field(default_factory=list)
    memory_ids: List[str] = field(default_factory=list)
    created_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "owner_type": self.owner_type,
            "owner_id": self.owner_id,
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "run_id": self.run_id,
            "messages": self.messages,
            "memory_ids": self.memory_ids,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BoxSnapshot":
        return cls(
            owner_type=d["owner_type"],
            owner_id=d["owner_id"],
            user_id=d.get("user_id"),
            agent_id=d.get("agent_id"),
            run_id=d.get("run_id"),
            messages=list(d.get("messages", [])),
            memory_ids=list(d.get("memory_ids", [])),
            created_at=d.get("created_at"),
        )


class MemBoxSealHandler:
    """membox 密封任务处理器:从 payload(BoxSnapshot)提取话题并落库。"""

    def __init__(self, llm, embedding_model, membox_store, pending_queue: Optional[MemBoxPendingQueue] = None) -> None:
        self.llm = llm
        self.embedding_model = embedding_model
        self._membox_store = membox_store
        self._pending_queue = pending_queue
        self._owner_locks: Dict[tuple, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _get_owner_lock(self, key: tuple) -> threading.Lock:
        with self._locks_guard:
            if key not in self._owner_locks:
                self._owner_locks[key] = threading.Lock()
            return self._owner_locks[key]

    def handle(self, user_id: str, payload: Dict[str, Any]) -> str | None:
        box = BoxSnapshot.from_dict(payload)
        owner_key = (box.user_id, box.agent_id, box.run_id)
        logger.info("Membox seal start owner=%s uid=%s aid=%s rid=%s msgs=%d", box.owner_id, box.user_id, box.agent_id, box.run_id, len(box.messages))
        with self._get_owner_lock(owner_key):
            box_id = self._seal_now(box)
        # push pending_queue 供 overall 合并(dual-owner push 两次)
        if box_id and self._pending_queue is not None:
            if box.user_id:
                self._pending_queue.push(box_id, "user", box.user_id)
            if box.agent_id:
                self._pending_queue.push(box_id, "agent", box.agent_id)
        return box_id

    def _call_llm(self, prompt: str) -> str:
        return self.llm.generate_response(messages=[{"role": "user", "content": prompt}], response_format={"type": "json_object"})

    def _run_pextract(self, dialog_text: str) -> Dict[str, Any]:
        prompt = build_membox_pextract_prompt(dialog_text)
        result = call_with_parse_retry(
            self._call_llm, prompt, parse_pextract_result,
            max_retries=_MEMBOX_PEXTRACT_RETRIES,
            operation_name="Membox Pextract",
            is_empty=lambda r: not (r.get("topic") or r.get("keywords") or r.get("explicit_mentions")),
        )
        return result or {}

    def _compose_content(self, topic: str, events: List[str], keywords: List[str], fallback_text: str) -> str:
        if topic or events or keywords:
            parts = [topic]
            if events:
                parts.append("事件: " + "; ".join(events))
            if keywords:
                parts.append("关键词: " + ", ".join(keywords))
            return "\n".join(parts)
        return fallback_text[:600] or "（空话题）"

    def _seal_now(self, box: BoxSnapshot) -> Optional[str]:
        if not box.messages:
            return None
        dialog_text = "\n".join(box.messages)
        try:
            result = self._run_pextract(dialog_text)
        except Exception:
            logger.exception("Membox Pextract failed, fallback content=dialog head")
            result = {}
        topic = result.get("topic") or ""
        keywords = result.get("keywords") or []
        events = result.get("explicit_mentions") or []

        now = now_iso()
        content = self._compose_content(topic, events, keywords, dialog_text)
        created_at = box.created_at or now

        owner_fields: Dict[str, Any] = {}
        if box.user_id:
            owner_fields["user_id"] = box.user_id
        if box.agent_id:
            owner_fields["agent_id"] = box.agent_id

        payload = {
            "content": content,
            "topic": topic,
            "memory_ids": box.memory_ids,
            "created_at": created_at,
            "updated_at": now,
            "run_id": box.run_id,
            **owner_fields,
        }
        try:
            vector = self.embedding_model.embed(content, "add")
        except Exception:
            logger.exception("membox embed failed, skip write")
            return None
        box_id = str(uuid.uuid4())
        try:
            self._membox_store.upsert([box_id], [vector], [payload])
        except Exception:
            logger.exception("membox upsert failed box_id=%s", box_id)
            return None
        logger.info("Membox seal done box_id=%s topic=%r", box_id, topic[:40])
        return box_id


def register_membox_handler(llm, embedding_model, membox_store, pending_queue=None) -> None:
    from ..tasks.handlers import register_handler

    register_handler(MEMBOX_CATEGORY, MEMBOX_TASK_TYPE, MemBoxSealHandler(llm, embedding_model, membox_store, pending_queue))
