"""策略三类型:profile / fact / summary(+ global 仅后台直改表,不暴露 API)。

模板作为 user_prompt/strategy_prompt 注入对应系统 prompt:
  - profile → 画像两步提取/比对 prompt
  - fact   → 事实 custom_instructions 插槽(global 合并后注入)
  - summary → 摘要 prompt
迁移自参考库 release-v2.2 ksc-mem0/mem0_server/strategy/types.py。
"""

from typing import Any, Dict, List

# 类型枚举(与 store 的 strategy_type 一致)。global 不暴露在类型字典,
# 不能经 API 新建或按类型查询,仅后台直改表;store 内部识别 global 拼入 merged_prompt。
STRATEGY_TYPES = ("profile", "fact", "summary")

_STRATEGY_TYPE_GLOBAL = "global"

PROFILE_STRATEGY_TEMPLATE = """请从对话中提取值得长期保留的用户画像,重点关注:
1. 用户相对稳定的身份与客观属性,如职业、常住地、技能、长期状态;
2. 用户与 AI 助手协作时的稳定习惯,如回复风格、格式、代码与解释要求;
3. 用户长期稳定的生活偏好、兴趣、习惯和价值倾向。

提取要求:
- 仅提取用户本人明确陈述,或由原文直接且唯一支持的信息,禁止猜测和扩展;
- 忽略转述的他人信息、通用知识、临时闲聊、一次性行为和短期计划;
- 每条只表达一个画像主题,使用简洁短语,保留地点、产品、版本、频率等关键细节;
- 相同语义合并,冲突信息以最新且更明确的用户陈述为准;
- 本指令只补充画像内容侧重,不改变系统定义的类别、长度限制和输出格式。"""


FACT_STRATEGY_TEMPLATE = """请从对话中完整提取客观、可追溯的原子事实,重点关注:
1. 明确发生的事件、人物行为、状态变化及其结果;
2. 用户明确表达的意图、需求、计划、决定、承诺和约束;
3. 对后续理解有价值的实体、时间、地点、数量、版本号和因果关系;
4. 对话中明确表达且影响事件理解的情绪或态度。

提取要求:
- 每条事实只表达一个独立含义;复合事件拆成多个可单独理解的事实;
- 忠于原文并明确说话人或事实归属,不把助手、第三方或引用内容错误归因给用户;
- 保留具体时间、专有名词、数值和状态,不用笼统概括替代原始细节;
- 不提取通用知识、无信息量寒暄、重复内容或无法由原文支持的推断;
- 本指令只补充事实内容侧重,不改变系统定义的时间规范、完整性规则和输出格式。"""


SUMMARY_STRATEGY_TEMPLATE = """请对会话核心内容进行简洁、客观、可延续的结构化总结,重点保留:
1. 会话主题、用户目标和当前进展;
2. 已确认的事实、重要结论、决定、共识与约束;
3. 关键讨论过程及形成结论的必要因果链;
4. 尚未解决的问题、待确认事项和明确的下一步计划;
5. 对后续会话仍有价值的用户原话和历史背景。

总结要求:
- 抓住重点,避免逐句复述、重复表达和无信息量寒暄;
- 保持说话人、时间、专有名词、版本号、数量和结论准确,不得臆造;
- 区分最终结论与讨论过程,优先体现最新进展;
- 用户长期稳定画像由画像系统维护,摘要只保留理解本会话所需的最小相关背景;
- 本指令只补充摘要内容侧重,不改变系统定义的章节枚举和输出格式。"""


# 类型字典:label=卡片标题、value=隐式标识、description=卡片描述、template=默认 Prompt 模板
_STRATEGY_TYPE_DICT: List[Dict[str, Any]] = [
    {
        "label": "画像记忆",
        "value": "profile",
        "description": "提取用户长期稳定的属性与偏好,如基础信息、工作偏好、决策风格等",
        "template": PROFILE_STRATEGY_TEMPLATE,
    },
    {
        "label": "事实记忆",
        "value": "fact",
        "description": "提取客观、可追溯的事实信息,如人物属性、实体信息、已发生事件等",
        "template": FACT_STRATEGY_TEMPLATE,
    },
    {
        "label": "会话摘要",
        "value": "summary",
        "description": "提取本次会话中值得长期保留的关键信息,如决定、计划、结论等",
        "template": SUMMARY_STRATEGY_TEMPLATE,
    },
]


def get_strategy_types() -> List[Dict[str, Any]]:
    """返回策略类型字典列表(副本,避免外部修改内部常量)。"""
    return [dict(item) for item in _STRATEGY_TYPE_DICT]
