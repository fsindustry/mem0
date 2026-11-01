"""共享记忆抽取 prompt + actions 解析。

迁移自参考库 shared_space/prompts.py,简化:固定 system 模板(含规则占位符),
actions 输出 JSON 对象 {"actions": [{"type":"CREATE","content":"..."}]}。
"""

import json
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """你是共享记忆抽取助手。

任务:从一批源事实(已按空间成员归属筛选)中,提炼出值得在该共享空间长期保留的共享记忆。

## 空间规则(可选,用户自定义侧重)
{rules}

## 输出格式
严格输出一个合法 JSON 对象,仅含键 "actions",不要 markdown 代码块、不要解释:
{{"actions": [{{"type": "CREATE", "content": "<共享记忆内容>"}}]}}

action.type 仅支持 CREATE(新建共享记忆)。每条 content 为一条独立的共享记忆,简洁客观,
保留关键实体/时间/结论,去重去冗余。无值得保留的内容时输出 {{"actions": []}}。
"""


def build_shared_extract_messages(batch_facts: List[Dict[str, Any]], rules_text: str = "") -> List[Dict[str, str]]:
    """渲染抽取 messages:system(含规则)+ user(批内事实 JSON)。"""
    facts_brief = [
        {"content": f.get("content") or f.get("data") or "", "user_id": f.get("user_id", ""), "agent_id": f.get("agent_id", ""), "created_at": f.get("created_at", "")}
        for f in batch_facts
    ]
    return [
        {"role": "system", "content": _SYSTEM_PROMPT.format(rules=(rules_text.strip() if rules_text.strip() else "（无）"))},
        {"role": "user", "content": json.dumps(facts_brief, ensure_ascii=False)},
    ]


def parse_extract_actions(raw: str) -> List[Dict[str, Any]]:
    """解析 LLM 输出为 actions 列表。非 JSON/缺字段/类型不符抛 ValueError。"""
    from ..profile.review import _extract_first_json

    val = _extract_first_json(raw, prefer_array=False)
    if not isinstance(val, dict) or set(val.keys()) != {"actions"}:
        raise ValueError("LLM output must be a single JSON object with only key 'actions'")
    actions_raw = val["actions"]
    if not isinstance(actions_raw, list):
        raise ValueError("actions must be an array")
    actions: List[Dict[str, Any]] = []
    for item in actions_raw:
        if not isinstance(item, dict):
            raise ValueError(f"action item must be dict: {item!r}")
        atype = item.get("type")
        if atype != "CREATE":
            raise ValueError(f"unknown action type: {atype!r}")
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("CREATE action requires non-empty content")
        actions.append({"type": "CREATE", "content": content.strip()})
    return actions
