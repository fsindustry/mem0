"""摘要 prompt:7 章节结构化 JSON 数组输出。

迁移自参考库 summary/prompts.py。章节枚举固定 7 类:
overview / new_facts / analysis / quotes / background / open_items / next_steps。
输出 JSON 数组,每条 {section, content};单点章节一条,多点章节每点一条。
"""

import logging
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

# (枚举值, 中文名, 是否多点)
SUMMARY_SECTIONS: Tuple[Tuple[str, str, bool], ...] = (
    ("overview", "一句话概述", False),
    ("new_facts", "本存档新增信息", True),
    ("analysis", "当前存档分析", True),
    ("quotes", "关键用户发言", True),
    ("background", "延续的历史背景", True),
    ("open_items", "未决事项", True),
    ("next_steps", "下一步", True),
)
SUMMARY_SECTION_KEYS = tuple(s[0] for s in SUMMARY_SECTIONS)
_SECTION_NAMES = {s[0]: s[1] for s in SUMMARY_SECTIONS}

SUMMARY_SYSTEM_PROMPT = """分析以下会话存档并生成结构化摘要。

先在内心梳理主题、新增结论与可延续上下文,再输出。输出为单个 JSON 数组,首字符 `[`、末字符 `]`,前后不得有任何字符。每个元素形如:
{{"section": "<章节枚举>", "content": "<单点文本>"}}

## 通用约束
- 语言:与对话主导语言一致;专有名词、版本号、文件名、错误码、API 名、代码标识符保留原文。quotes 保留原始措辞,其余章节遵循本规则。
- 格式:禁换行、禁序号或项目符号;句号结尾。content 内禁未转义的 ASCII 双引号,原话含引号用中文引号或 \\\" 转义。

## 章节(按枚举顺序输出,无内容的章节省略,不得跳序/新增/更改枚举)
- overview(一句话概述):本会话核心主题与进展一句话。必须输出。
- new_facts(本存档新增信息):本次明确的结论、决定、共识、约束、新事实。每点一条。
- analysis(当前存档分析):存档内推进过程与因果链(谁说了什么导致下一步)。只写过程,不含最终结论词。无明显推进则省略。每条对应一条独立推进线。
- quotes(关键用户发言):值得未来直接引用的用户原话。保留原始措辞。
- background(延续的历史背景):对后续会话仍有价值的用户原话和历史背景。
- open_items(未决事项):尚未解决的问题、待确认事项。
- next_steps(下一步):明确的下一步计划。

## 自定义指令(可选,仅补充内容侧重与风格,不得改变上述系统部分)
{user_prompt}

## 旧摘要(可选,延续上下文)
{old_summary}

## 新记录
{new_records}
"""


def build_summary_user_prompt(old_summary: str, new_records: str, user_prompt: str = "") -> str:
    return SUMMARY_SYSTEM_PROMPT.format(
        user_prompt=(user_prompt.strip() if user_prompt and user_prompt.strip() else "（无）"),
        old_summary=(old_summary.strip() if old_summary and old_summary.strip() else "（无）"),
        new_records=(new_records.strip() if new_records and new_records.strip() else "（无）"),
    )


def parse_summary_result(raw: str) -> List[Dict[str, Any]]:
    """解析摘要 LLM 输出为 [{section, content}],校验 section 枚举。"""
    from ..profile.review import _extract_first_json

    val = _extract_first_json(raw, prefer_array=True)
    if not isinstance(val, list):
        return []
    items: List[Dict[str, Any]] = []
    for it in val:
        if not isinstance(it, dict):
            continue
        section = it.get("section")
        content = it.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        if section not in SUMMARY_SECTION_KEYS:
            continue
        items.append({"section": section, "content": content.strip()})
    return items
