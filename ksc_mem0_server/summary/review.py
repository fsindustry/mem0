"""SummaryReviewer:摘要 LLM 调用与格式化辅助。

迁移自参考库 summary/review.py,简化:generate_strategy 单策略生成(返回 entries 列表),
runtime 对多策略 fan-out 调用本方法。旧摘要按 SUMMARY_SECTIONS 顺序聚合。
"""

import logging
from typing import Any, Dict, List

from ..profile.review import call_with_parse_retry
from .prompts import SUMMARY_SECTIONS, build_summary_user_prompt, parse_summary_result

logger = logging.getLogger(__name__)


class SummaryReviewer:
    def __init__(self, llm, embedding_model, summary_store, strategy_store) -> None:
        self.llm = llm
        self.embedding_model = embedding_model
        self._summary_store = summary_store
        self._strategy_store = strategy_store

    def _call_llm(self, prompt: str) -> str:
        messages = [{"role": "user", "content": prompt}]
        return self.llm.generate_response(messages=messages, response_format={"type": "json_object"})

    def format_memories(self, memories: List[Dict[str, Any]]) -> str:
        lines = []
        for m in memories:
            text = m.get("text") or m.get("data") or m.get("content") or ""
            ts = m.get("updated_at") or m.get("created_at") or ""
            if text:
                lines.append(f"[{ts}] {text}" if ts else text)
        return "\n".join(lines)

    def fetch_old_summary(self, user_id: str, run_id: str, strategy_id: str) -> str:
        """取该 user+run+strategy 已有摘要,按章节顺序聚合为文本(供 prompt 旧摘要)。"""
        items = self._summary_store.list(
            filters={"user_id": user_id, "run_id": run_id, "strategy_id": strategy_id}, limit=500
        )
        by_section: Dict[str, List[str]] = {}
        for it in items:
            payload = it.get("payload") or {}
            sec = payload.get("section")
            data = payload.get("data") or payload.get("content") or ""
            if sec and data:
                by_section.setdefault(sec, []).append(data)
        parts = []
        for key, _name, _multi in SUMMARY_SECTIONS:
            for text in by_section.get(key, []):
                parts.append(f"[{_name}] {text}")
        return "\n".join(parts)

    def generate_strategy(
        self, strategy: Dict[str, str], user_id: str, run_id: str, inputs: List[Dict[str, Any]]
    ) -> List[Dict[str, str]]:
        """单策略生成摘要 entries [{section, content}]。"""
        strategy_id = strategy.get("strategy_id", "default")
        strategy_name = strategy.get("name", strategy_id)
        user_prompt = strategy.get("prompt", "")
        old_summary = self.fetch_old_summary(user_id, run_id, strategy_id)
        new_records = self.format_memories(inputs)
        prompt = build_summary_user_prompt(old_summary=old_summary, new_records=new_records, user_prompt=user_prompt)
        entries = call_with_parse_retry(
            self._call_llm, prompt, parse_summary_result,
            max_retries=2, operation_name=f"Summary generate {strategy_name}",
            is_empty=lambda r: not r,
        )
        return entries or []

    def resolve_strategy_name_prompt(self, strategy_id: str) -> tuple:
        s = self._strategy_store.strategy_store.get_active_strategy_by_id("summary", strategy_id) if hasattr(self._strategy_store, "strategy_store") else None
        if s is None:
            return strategy_id, ""
        return s.get("name", strategy_id), s.get("prompt", "")
