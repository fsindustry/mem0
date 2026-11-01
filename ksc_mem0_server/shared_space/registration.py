"""SharedSpaceRegistrar:消费登记器(fact 落库后登记 pending → 阈值触发)。

迁移自参考库 shared_space/registration.py(MySQL→PG)。职责:
  1. fact 落库后按成员归属反查空间,逐空间幂等登记 pending,累计 pending_tokens;
  2. pending_tokens 达阈值时经幂等入口触发抽取任务(InFlightGate + run_lease);
  3. fact 删除/替换:作废未完成登记并回退 pending_tokens。
trigger 由 scheduler.make_trigger 注入(阈值与 cron 兜底共用同一入口)。
"""

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .store import SharedSpaceStore
from .tokens import estimate_tokens

logger = logging.getLogger(__name__)


def compute_source_version(text: str, user_id: str = "", agent_id: str = "", run_id: str = "", created_at: str = "") -> str:
    """规范化计算源版本 fingerprint(sha256 前 32 位)。"""
    normalized = "\x1f".join([(text or "").strip(), user_id or "", agent_id or "", run_id or "", created_at or ""])
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


def _parse_iso(val: str) -> Optional[datetime]:
    if not val:
        return None
    try:
        dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class SharedSpaceRegistrar:
    """消费登记器:store(MySQL→PG)+ memory_provider(读 config)+ trigger(注入)。"""

    def __init__(self, store: SharedSpaceStore, memory_provider, trigger=None) -> None:
        self._store = store
        self._memory_provider = memory_provider
        self._trigger = trigger

    def on_facts_persisted(self, records: List[tuple]) -> None:
        """fact 批量落库后登记。records 为 (memory_id, text, embedding, payload)。异常仅日志不阻断。"""
        if not records:
            return
        try:
            token_estimator = getattr(self._memory_provider().config, "shared_space_token_estimator", "char_weighted")
        except Exception:
            logger.exception("[shared_space_register] memory_provider unavailable, skip")
            return
        try:
            if not self._store.list_enabled_spaces():
                return
        except Exception:
            logger.exception("[shared_space_register] enabled-space check failed, skip")
            return
        for record in records:
            try:
                fact_id, text, payload = record[0], record[1], (record[3] or {})
                if not fact_id or not (text or "").strip():
                    continue
                user_id = payload.get("user_id") or ""
                agent_id = payload.get("agent_id") or ""
                self._register_fact(fact_id, text, payload, user_id, agent_id, token_estimator)
            except Exception:
                logger.exception("[shared_space_register] register fact failed fact_id=%s", record[0] if record else None)

    def _register_fact(self, fact_id: str, text: str, payload: Dict[str, Any], user_id: str, agent_id: str, token_estimator: str = "char_weighted") -> None:
        space_ids = self._resolve_space_ids(user_id, agent_id)
        if not space_ids:
            return
        created_at = payload.get("created_at") or ""
        source_version = compute_source_version(text, user_id=user_id, agent_id=agent_id, run_id=payload.get("run_id") or "", created_at=created_at)
        tokens = estimate_tokens(text, token_estimator)
        source_created_at = _parse_iso(created_at)
        for space_id in space_ids:
            self._register_to_space(space_id, fact_id, source_version, tokens, source_created_at, user_id, agent_id)

    def _resolve_space_ids(self, user_id: str, agent_id: str) -> List[str]:
        space_ids: List[str] = []
        if user_id:
            space_ids.extend(self._store.list_member_space_ids(user_id, "user"))
        if agent_id:
            space_ids.extend(self._store.list_member_space_ids(agent_id, "agent"))
        seen, deduped = set(), []
        for sid in space_ids:
            if sid and sid not in seen:
                seen.add(sid)
                deduped.append(sid)
        return deduped

    def _register_to_space(self, space_id: str, fact_id: str, source_version: str, tokens: int, source_created_at: Optional[datetime], user_id: str, agent_id: str) -> None:
        space = self._store.get_space(space_id)
        if not space or not space.get("extract_enabled"):
            return
        if not self._store.register_pending(space_id, fact_id, source_version, tokens, source_created_at, user_id=user_id, agent_id=agent_id):
            return  # 已登记(幂等),不重复累计 token
        if self._store.adjust_pending_tokens(space_id, tokens):
            updated_space = self._store.get_space(space_id) or space
            self._maybe_trigger(updated_space)

    def _maybe_trigger(self, space: Dict[str, Any]) -> None:
        try:
            threshold = self._threshold_for(space)
            if int(space.get("pending_tokens") or 0) >= threshold:
                self.trigger_space(space["space_id"])
        except Exception:
            logger.exception("[shared_space_register] trigger check failed space=%s", space.get("space_id"))

    def _threshold_for(self, space: Dict[str, Any]) -> int:
        if space.get("token_threshold"):
            return int(space["token_threshold"])
        return int(getattr(self._memory_provider().config, "shared_space_token_threshold", 2000))

    def trigger_space(self, space_id: str) -> bool:
        if self._trigger is None:
            return False
        from .scheduler import invoke_trigger

        return invoke_trigger(self._trigger, space_id, "threshold")

    def on_fact_deleted(self, fact_id: str) -> None:
        """fact 删除/替换:作废未完成登记并回退 pending_tokens。"""
        try:
            reclaimed = self._store.obsolete_pending(fact_id)
            for space_id, tokens in reclaimed:
                if tokens > 0:
                    self._store.adjust_pending_tokens(space_id, -tokens)
        except Exception:
            logger.exception("[shared_space_register] obsolete failed fact_id=%s", fact_id)
