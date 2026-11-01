"""digests prompts:membox pextract(密封提取)+ merge(合并)。

迁移自参考库 digests/prompts.py,保留 pextract 的 {keywords,topic,explicit_mentions} JSON 契约
与 merge 的纯文本输出契约,精简长文案。
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

MEMBOX_PEXTRACT_PROMPT = """你是对话结构化分析助手。

任务:对提供的对话生成结构化分析,输出 keywords、topic、explicit_mentions 三项。

核心要点:topic 只陈述主题、不加评价;explicit_mentions 只收录明确的事件/事实/计划、去重、不推断;keywords 选具体术语、不含日期。

1. keywords:3-8 个最能代表核心概念的名词、命名实体与关键术语;避免常见词,优先选具体、信息量高的词;不得包含具体日期或时间。
2. topic:用一句清晰的话陈述讨论的主要主题或目标。只陈述主题本身,禁止评价或引申。
3. explicit_mentions:仅列出对话中明确提及的事件、既定事实变化或具体未来计划。每条须满足:可在不依赖当前对话上下文的情况下独立理解。

输出格式:只输出一个合法 JSON 对象,不要 markdown 代码块、不要解释或前后缀。严格使用以下键:
{{"keywords": ["<关键词1>", "<关键词2>"], "topic": "<清晰的主题短语>", "explicit_mentions": ["<过去事件或既定事实>", "<具体未来计划>"]}}

## 对话
{dialog}
"""

DIGEST_MERGE_PROMPT = """你是话题合并助手。

任务:将新话题内容合并到已有话题摘要中,生成更新后的场景摘要。

要求:
- 第三人称客观叙述。开篇一句话点明共同主题并概览聚合规模,再围绕主题分层组织。
- 提炼共性、指出例外;不按时间或人物流水账。
- 输出一段文本,不要 JSON、不要 markdown 代码块、不要解释或前后缀。

## 已有摘要
{old_content}

## 新话题
{new_topic}
{new_content}

## 关键词
{keywords}
"""


def build_membox_pextract_prompt(dialog: str) -> str:
    return MEMBOX_PEXTRACT_PROMPT.format(dialog=dialog.strip() if dialog.strip() else "（无）")


def build_digest_merge_prompt(old_content: str, new_topic: str, new_content: str, keywords: List[str]) -> str:
    return DIGEST_MERGE_PROMPT.format(
        old_content=(old_content.strip() if old_content.strip() else "（无）"),
        new_topic=(new_topic.strip() if new_topic.strip() else "（无）"),
        new_content=(new_content.strip() if new_content.strip() else "（无）"),
        keywords=", ".join(keywords) if keywords else "（无）",
    )


def parse_pextract_result(raw: str) -> Dict[str, Any]:
    """解析 Pextract LLM 输出为 {keywords, topic, explicit_mentions}。"""
    from ..profile.review import _extract_first_json

    val = _extract_first_json(raw, prefer_array=False)
    if not isinstance(val, dict):
        return {}

    def _as_str_list(v: Any) -> List[str]:
        if not isinstance(v, list):
            return []
        out = []
        for x in v:
            if isinstance(x, str) and x.strip():
                out.append(x.strip())
        return out

    keywords = _as_str_list(val.get("keywords"))
    topic = val.get("topic")
    topic = topic.strip() if isinstance(topic, str) else ""
    mentions = _as_str_list(val.get("explicit_mentions"))
    if not keywords and not topic and not mentions:
        return {}
    return {"keywords": keywords, "topic": topic, "explicit_mentions": mentions}
