"""SharedSpaceExtractHandler:批次抽取闭环(claim → fetch → LLM 抽取 → apply CREATE → index → finish)。

迁移自参考库 shared_space/handler.py,简化:
  - 单轮抽取(跳过 _should_continue 多轮续抽 + history/memory 上下文收集 + representative_vectors)
  - fetch 直接从 facts 的 pending 记录取 content(参考库从主记忆 vector_store fetch,
    简化为 pending 登记时已可带 content;此处从 pending user/agent_id + fact_id 构造 brief)
  - 只支持 CREATE 动作(参考库含 UPDATE/DELETE,留 P5)
  - 失败回退 pending + 租约释放 + gate release
保留核心:claim_pending → LLM 抽取 actions → apply CREATE → index pgvector → finish_pending / release。
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from .prompts import build_shared_extract_messages, parse_extract_actions
from .store import SharedSpaceStore
from .vector_store import SharedVectorStore

logger = logging.getLogger(__name__)

_ACTION_TYPES = ("CREATE",)
DEFAULT_BATCH_FACT_COUNT = 50


class SharedSpaceExtractHandler:
    """共享空间抽取任务处理器:从队列取 space_id → 抽取一轮 → 产出共享记忆。"""

    def __init__(
        self,
        llm,
        embedding_model,
        store: SharedSpaceStore,
        vector_store: SharedVectorStore,
        gate,
        memory_provider: Optional[Callable] = None,
        batch_fact_count: int = DEFAULT_BATCH_FACT_COUNT,
    ) -> None:
        self.llm = llm
        self.embedding_model = embedding_model
        self._store = store
        self._vector_store = vector_store
        self._gate = gate
        self._memory_provider = memory_provider
        self._batch_fact_count = batch_fact_count

    def handle(self, user_id: str, payload: Dict[str, Any], runtime: Any = None) -> None:
        """user_id = space_id(队列路由)。payload 含 space_id + lease_token。"""
        space_id = payload.get("space_id") or user_id
        lease_token = payload.get("lease_token")
        try:
            self._extract_round(space_id)
        finally:
            # 释放租约 + gate
            if lease_token:
                self._store.release_run_lease(space_id, lease_token)
            self._gate.release(space_id)

    def _extract_round(self, space_id: str) -> bool:
        space = self._store.get_space(space_id)
        if not space or not space.get("extract_enabled"):
            return False
        candidates = self._store.list_pending_candidates(space_id, self._batch_fact_count)
        if not candidates:
            return False
        batch_id = str(uuid.uuid4())
        fact_ids = [c["fact_id"] for c in candidates]
        claimed = self._store.claim_pending(space_id, fact_ids, batch_id)
        if not claimed:
            return False
        claimed_set = set(claimed)
        selected = [c for c in candidates if c["fact_id"] in claimed_set]

        try:
            actions = self._llm_extract(space_id, selected)
            produced = self._apply_actions(space_id, batch_id, actions)
            # finish pending(标记 done)
            self._store.finish_pending(space_id, claimed, [c["source_version"] for c in selected])
            # 重置 pending_tokens(已消费的 tokens 不再计入;finish 后空间积压清减)
            consumed = sum(c["tokens"] for c in selected)
            if consumed > 0:
                self._store.adjust_pending_tokens(space_id, -consumed)
            self._store.update_extract_progress(space_id, datetime.now(timezone.utc))
            self._store.update_batch(batch_id, {"status": "done", "rounds": 1})
            logger.info("Shared space extract done space=%s batch=%s produced=%d consumed=%d", space_id, batch_id, produced, consumed)
            return True
        except Exception:
            logger.exception("Shared space extract failed space=%s batch=%s, release pending", space_id, batch_id)
            self._store.release_pending(space_id, claimed)
            self._store.update_batch(batch_id, {"status": "failed"})
            return False

    def _llm_extract(self, space_id: str, selected: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """LLM 抽取 actions。selected 为 pending 候选(含 fact_id/source_version/tokens/user_id/agent_id)。

        简化:pending 登记时未带 content,此处用 fact_id 作 content 占位(真实 content 从主记忆
        fetch 留 P5;当前用 source 信息构造 brief,LLM 据 brief 产出共享记忆)。
        """
        batch_facts = [
            {"content": f.get("content") or f"fact:{f['fact_id']}", "user_id": f.get("user_id", ""), "agent_id": f.get("agent_id", ""), "created_at": str(f.get("source_created_at") or "")}
            for f in selected
        ]
        rules = self._load_rules(space_id)
        messages = build_shared_extract_messages(batch_facts, rules)
        raw = self.llm.generate_response(messages=messages, response_format={"type": "json_object"})
        return parse_extract_actions(raw) if raw else []

    def _load_rules(self, space_id: str) -> str:
        rules = self._store.list_all_rules(space_id)
        return "\n".join(r["content"] for r in rules) if rules else ""

    def _apply_actions(self, space_id: str, batch_id: str, actions: List[Dict[str, Any]]) -> int:
        produced = 0
        output_records = []
        for action in actions:
            if action["type"] == "CREATE":
                self._apply_create(space_id, action["content"])
            output_records.append(action)
            produced += 1
        self._store.update_batch(batch_id, {"output_snapshot": json.dumps(output_records, ensure_ascii=False, default=str)})
        return produced

    def _apply_create(self, space_id: str, content: str) -> None:
        memory_id = str(uuid.uuid4())
        if not self._index_shared_memory(space_id, memory_id, content):
            raise ValueError(f"failed to persist shared memory space_id={space_id}")

    def _index_shared_memory(self, space_id: str, memory_id: str, content: str) -> bool:
        try:
            vector = self.embedding_model.embed(content, "add")
            self._vector_store.upsert(
                [memory_id], [vector],
                [{"space_id": space_id, "content": content, "memory_kind": "generated", "version": 1, "extract_time": datetime.now(timezone.utc).isoformat()}],
            )
            return True
        except Exception:
            logger.exception("Shared memory index failed space=%s memory=%s", space_id, memory_id)
            return False


def register_shared_space_handler(handler: SharedSpaceExtractHandler) -> None:
    from ..tasks.handlers import register_handler
    from .scheduler import SHARED_SPACE_CATEGORY, SHARED_SPACE_TASK_TYPE

    register_handler(SHARED_SPACE_CATEGORY, SHARED_SPACE_TASK_TYPE, handler)
