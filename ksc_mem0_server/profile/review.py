"""画像 review 纯函数 + ProfileReviewer(LLM 调用与格式化辅助)。

迁移自参考库 profile/review.py。纯函数(解析/合并/热度/裁剪)完整保留;
ProfileReviewer 简化:不依赖 source_journal,新记忆由触发方经 task_store.register
注入 run["inputs"];旧画像从 profile_store.list 取。

两步 LLM:EXTRACT(新记录→P[{data,category}]) → COMPARE(P vs 旧画像→{updated,added})。
"""

import hashlib
import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from .store import PROFILE_CATEGORIES

logger = logging.getLogger(__name__)

DEFAULT_REVIEW_BATCH_SIZE = 100
DEFAULT_REVIEW_RETRIES = 2
NEW_MEMORIES_TOP_K = 200

MENTION_SAT = 10
HEAT_MC_WEIGHT = 0.5
HEAT_RECENCY_WEIGHT = 0.5
HEAT_TAU = {"identity": 365.0, "agent_habit": 90.0, "preference": 180.0}

DEFAULT_CATEGORY_LIMITS = {"identity": 40, "agent_habit": 20, "preference": 40}
DEFAULT_TOTAL_LIMIT = 100
DEFAULT_CONSTRAINTS = {"length_identity": 30, "length_agent_habit": 20, "length_preference": 30}


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """从 dict 或对象(含 payload 属性)取值,统一 Milvus {id,payload} 与扁平形态。"""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _parse_iso(val: Any) -> Optional[datetime]:
    if not val:
        return None
    try:
        dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _coerce_mention_count(val: Any) -> Optional[int]:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return int(val)
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        try:
            return int(s)
        except ValueError:
            try:
                return int(float(s))
            except ValueError:
                return None
    return None


def _compute_heat_score(category: str, mention_count: int, updated_at: Optional[datetime], now: datetime) -> int:
    mc_factor = min(1.0, (mention_count or 0) / MENTION_SAT)
    tau = HEAT_TAU.get(category)
    if updated_at is None:
        recency_factor = 1.0
    else:
        ua = updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=timezone.utc)
        delta_days = max(0.0, (now - ua).total_seconds() / 86400.0)
        recency_factor = math.exp(-delta_days / tau) if tau else 1.0
    if category == "identity":
        score = mc_factor
    else:
        score = HEAT_MC_WEIGHT * mc_factor + HEAT_RECENCY_WEIGHT * recency_factor
    return round(max(0.0, min(1.0, score)) * 100)


# --- JSON 提取 ---
def _extract_first_json(raw: str, prefer_array: bool = False) -> Any:
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```", 2)
        text = parts[1] if len(parts) >= 3 else (parts[-1] if parts else "")
        text = text.removeprefix("json").strip()
    obj_start = text.find("{")
    arr_start = text.find("[")
    if prefer_array:
        start = arr_start if arr_start != -1 else obj_start
    else:
        start = obj_start if obj_start != -1 else arr_start
    if start == -1:
        return None
    open_ch = text[start]
    close_ch = "}" if open_ch == "{" else "]"
    depth = 0
    in_str = False
    esc = False
    end = -1
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _filter_p_items(items: Any) -> List[Dict[str, Any]]:
    if not isinstance(items, list):
        return []
    p_list: List[Dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        category = it.get("category")
        data = it.get("data")
        if data is None:
            continue
        if not isinstance(data, str):
            data = str(data)
        data = data.strip()
        if not data or category not in PROFILE_CATEGORIES:
            continue
        p_list.append({"data": data, "category": category})
    return p_list


def parse_extract_result(raw: str) -> List[Dict[str, Any]]:
    val = _extract_first_json(raw, prefer_array=True)
    if isinstance(val, list):
        p = _filter_p_items(val)
        if p:
            return p
    obj = _extract_first_json(raw, prefer_array=False)
    if isinstance(obj, dict):
        p = _filter_p_items(obj.get("added"))
        if p:
            return p
    return []


def parse_compare_result(raw: str) -> Dict[str, List[Dict[str, Any]]]:
    empty: Dict[str, List[Dict[str, Any]]] = {"updated": [], "added": []}
    val = _extract_first_json(raw)
    if not isinstance(val, dict):
        return empty
    updated = [u for u in (val.get("updated") or []) if isinstance(u, dict) and u.get("id") is not None]
    added = []
    for a in (val.get("added") or []):
        if isinstance(a, dict):
            ref = a.get("ref")
            if isinstance(ref, str) and ref.strip():
                added.append({"ref": ref.strip()})
    return {"updated": updated, "added": added}


def _resolve_added_refs(added_refs: List[Dict[str, Any]], p_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    resolved: List[Dict[str, Any]] = []
    seen_idx: set = set()
    for a in added_refs:
        ref = a.get("ref") if isinstance(a, dict) else None
        if not isinstance(ref, str):
            continue
        ref_norm = ref.strip().lower()
        if not ref_norm.startswith("p"):
            continue
        try:
            idx = int(ref_norm[1:])
        except (ValueError, TypeError):
            continue
        if idx < 0 or idx >= len(p_list) or idx in seen_idx:
            continue
        seen_idx.add(idx)
        p = p_list[idx]
        resolved.append({"data": p.get("data", ""), "category": p.get("category", "")})
    return resolved


def _temp_profile_id(data: str, category: str) -> str:
    h = hashlib.md5(f"{category}:{data}".encode()).hexdigest()[:12]
    return f"tmp_{h}"


def to_working_profile(old_profile: List[Any]) -> List[Dict[str, Any]]:
    working: List[Dict[str, Any]] = []
    for p in old_profile:
        pid = _get(p, "id")
        payload = _get(p, "payload") or {}
        category = _get(payload, "category")
        if category not in PROFILE_CATEGORIES:
            continue
        mc = _get(payload, "mention_count", 0)
        if mc < 0:
            continue
        working.append(
            {
                "id": pid or "",
                "data": _get(payload, "data", ""),
                "category": category,
                "mention_count": mc,
                "updated_at": _get(payload, "updated_at", ""),
            }
        )
    return working


def apply_profile_updates(
    working: List[Dict[str, Any]],
    updated: List[Dict[str, Any]],
    added: List[Dict[str, Any]],
    now: datetime,
) -> None:
    reinforce_delta: Dict[str, int] = {}
    touched_ids: set = set()
    invalidate_ids: set = set()
    for u in updated:
        pid = u.get("id")
        if not pid:
            continue
        mc = _coerce_mention_count(u.get("mention_count", 0))
        if mc is None:
            continue
        if mc < 0:
            invalidate_ids.add(pid)
        elif mc > 0:
            touched_ids.add(pid)
            reinforce_delta[pid] = reinforce_delta.get(pid, 0) + 1

    by_id: Dict[str, Dict[str, Any]] = {e["id"]: e for e in working if e.get("id")}
    for pid in invalidate_ids:
        e = by_id.get(pid)
        if e is not None:
            e["mention_count"] = -1
    now_iso = now.isoformat()
    for pid in touched_ids:
        e = by_id.get(pid)
        if e is not None and e.get("mention_count", 0) >= 0:
            e["mention_count"] = e["mention_count"] + reinforce_delta.get(pid, 0)
            e["updated_at"] = now_iso

    seen = {(e.get("data", ""), e.get("category")) for e in working if e.get("mention_count", 0) >= 0}
    for a in added:
        data = (a.get("data", "") or "").strip()
        category = a.get("category")
        if not data or category not in PROFILE_CATEGORIES:
            continue
        key = (data, category)
        if key in seen:
            continue
        seen.add(key)
        working.append(
            {
                "id": _temp_profile_id(data, category),
                "data": data,
                "category": category,
                "mention_count": 1,
                "updated_at": now_iso,
            }
        )


def working_to_entries(working: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for e in working:
        mc = e.get("mention_count", 0)
        if mc < 0:
            continue
        entry = {
            "data": e.get("data", ""),
            "category": e.get("category"),
            "mention_count": mc,
            "updated_at": e.get("updated_at", ""),
        }
        if "heat_score" in e:
            entry["heat_score"] = e["heat_score"]
        entries.append(entry)
    return entries


def compact_working_profile(
    working: List[Dict[str, Any]], now: datetime, limits: Dict[str, int], total_limit: int
) -> None:
    surviving = [e for e in working if e.get("mention_count", 0) >= 0]
    for e in surviving:
        ua = e.get("updated_at")
        ua_dt = _parse_iso(ua) if isinstance(ua, str) and ua else (ua if isinstance(ua, datetime) else now)
        e["heat_score"] = _compute_heat_score(e["category"], e.get("mention_count", 0), ua_dt, now)
    if len(surviving) <= total_limit:
        working[:] = surviving
        return
    kept: List[Dict[str, Any]] = []
    for cat in PROFILE_CATEGORIES:
        group = [e for e in surviving if e["category"] == cat]
        limit = limits.get(cat, 0)
        if limit and len(group) > limit:
            group.sort(key=lambda e: e["heat_score"])
            group = group[len(group) - limit :]
        kept.extend(group)
    overflow = len(kept) - total_limit
    if overflow > 0:
        kept.sort(key=lambda e: e["heat_score"])
        kept = kept[overflow:]
    working[:] = kept


def finalize_entries(
    entries: List[Dict[str, Any]], now: datetime, limits: Dict[str, int], total_limit: int
) -> List[Dict[str, Any]]:
    for e in entries:
        ua = e.get("updated_at")
        if not isinstance(ua, datetime):
            ua = _parse_iso(ua) if ua else now
            e["updated_at"] = ua
        if "heat_score" not in e:
            e["heat_score"] = _compute_heat_score(e["category"], e.get("mention_count", 0), ua, now)
    if len(entries) <= total_limit:
        return entries
    kept: List[Dict[str, Any]] = []
    for cat in PROFILE_CATEGORIES:
        group = [e for e in entries if e["category"] == cat]
        limit = limits.get(cat, 0)
        if limit and len(group) > limit:
            group.sort(key=lambda e: e["heat_score"])
            group = group[len(group) - limit :]
        kept.extend(group)
    overflow = len(kept) - total_limit
    if overflow > 0:
        kept.sort(key=lambda e: e["heat_score"])
        kept = kept[overflow:]
    return kept


def build_new_version(
    old_profile: List[Any],
    updated: List[Dict[str, Any]],
    added: List[Dict[str, Any]],
    now: datetime,
) -> List[Dict[str, Any]]:
    """合并旧画像与 LLM 更新生成新版本(单批等价;多批用 apply_profile_updates 滚动)。"""
    old_mc_by_id: Dict[str, int] = {}
    for item in old_profile:
        pid = _get(item, "id")
        if pid:
            payload = _get(item, "payload") or {}
            old_mc_by_id[pid] = _get(payload, "mention_count", 0)
    reinforce_delta: Dict[str, int] = {}
    touched_ids: set = set()
    invalidate_ids: set = set()
    for u in updated:
        pid = u.get("id")
        if not pid:
            continue
        mc = _coerce_mention_count(u.get("mention_count", 0))
        if mc is None:
            continue
        if mc < 0:
            invalidate_ids.add(pid)
        elif mc > 0:
            touched_ids.add(pid)
            reinforce_delta[pid] = reinforce_delta.get(pid, 0) + 1
    entries: List[Dict[str, Any]] = []
    for item in old_profile:
        pid = _get(item, "id")
        payload = _get(item, "payload") or {}
        if pid in invalidate_ids:
            continue
        if pid and pid in touched_ids:
            mc = old_mc_by_id.get(pid, 0) + reinforce_delta.get(pid, 0)
            updated_at = now
        else:
            mc = _get(payload, "mention_count", 0)
            if mc < 0:
                continue
            updated_at = _parse_iso(_get(payload, "updated_at")) or now
        data = _get(payload, "data", "") or ""
        category = _get(payload, "category")
        if not data or category not in PROFILE_CATEGORIES:
            continue
        entries.append({"data": data, "category": category, "mention_count": mc, "updated_at": updated_at})
    seen_added: set = set()
    for a in added:
        data = (a.get("data", "") or "").strip()
        category = a.get("category")
        if not data or category not in PROFILE_CATEGORIES:
            continue
        key = (data, category)
        if key in seen_added:
            continue
        seen_added.add(key)
        entries.append({"data": data, "category": category, "mention_count": 1, "updated_at": now})
    return entries


@dataclass
class ProfileReviewConfig:
    batch_size: int
    retries: int
    constraints: Dict[str, int]
    category_limits: Dict[str, int]
    total_limit: int

    @classmethod
    def default(cls) -> "ProfileReviewConfig":
        return cls(
            batch_size=DEFAULT_REVIEW_BATCH_SIZE,
            retries=DEFAULT_REVIEW_RETRIES,
            constraints=dict(DEFAULT_CONSTRAINTS),
            category_limits=dict(DEFAULT_CATEGORY_LIMITS),
            total_limit=DEFAULT_TOTAL_LIMIT,
        )


def call_with_parse_retry(
    call_llm: Callable[[str], str],
    prompt: str,
    parse: Callable[[str], Any],
    max_retries: int = 2,
    is_empty: Optional[Callable[[Any], bool]] = None,
    operation_name: str = "",
) -> Any:
    """调 LLM 并解析,失败/空指数退避重试。"""
    last = None
    for attempt in range(max_retries + 1):
        try:
            raw = call_llm(prompt)
            parsed = parse(raw)
            if is_empty is not None:
                if is_empty(parsed):
                    raise ValueError("empty result")
            else:
                if not parsed:
                    raise ValueError("empty result")
            return parsed
        except Exception as e:
            last = e
            logger.warning("%s attempt %d failed: %s", operation_name, attempt, e)
    logger.warning("%s exhausted retries, returning last parse", operation_name)
    # 返回最后一次解析结果(可能为空),不抛——由调用方判空跳过
    try:
        return parse(call_llm(prompt)) if last else []
    except Exception:
        return []


class ProfileReviewer:
    """画像 LLM 调用与格式化辅助。协调逻辑由 ProfileRuntime 持有。"""

    def __init__(self, llm, embedding_model, profile_store, strategy_store) -> None:
        self.llm = llm
        self.embedding_model = embedding_model
        self._profile_store = profile_store
        self._strategy_store = strategy_store

    def _call_llm(self, prompt: str) -> str:
        """调 mem0 LLM 生成文本(单消息,无 tools)。"""
        messages = [{"role": "user", "content": prompt}]
        return self.llm.generate_response(messages=messages, response_format={"type": "json_object"})

    def format_memories(self, memories: List[Dict[str, Any]]) -> str:
        """把新记忆 list 拼成 prompt 文本(每行一条)。"""
        lines = []
        for m in memories:
            text = m.get("text") or m.get("data") or m.get("content") or ""
            ts = m.get("updated_at") or m.get("created_at") or ""
            if text:
                lines.append(f"[{ts}] {text}" if ts else text)
        return "\n".join(lines)

    def format_profile_json(self, working: List[Dict[str, Any]]) -> str:
        """把工作画像按三类分组拼成 JSON(供 COMPARE prompt 的旧画像)。"""
        grouped: Dict[str, List[Dict[str, str]]] = {cat: [] for cat in PROFILE_CATEGORIES}
        for e in working:
            if e.get("mention_count", 0) < 0:
                continue
            cat = e.get("category")
            if cat in grouped:
                grouped[cat].append({"id": e.get("id", ""), "data": e.get("data", "")})
        return json.dumps(grouped, ensure_ascii=False)

    def fetch_old_profile(self, user_id: str, strategy_id: str, version: int) -> List[Dict[str, Any]]:
        """取该 user+strategy 当前版本的旧画像。"""
        return self._profile_store.list(
            filters={"user_id": user_id, "strategy_id": strategy_id, "version": version}, limit=500
        )

    def resolve_strategy_name_prompt(self, strategy_id: str, strategy_store) -> tuple:
        """反查策略 name + prompt(供落盘追溯 + user_prompt 注入)。"""
        s = strategy_store.get_active_strategy_by_id("profile", strategy_id)
        if s is None:
            return strategy_id, ""
        return s.get("name", strategy_id), s.get("prompt", "")
