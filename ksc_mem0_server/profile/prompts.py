"""画像两步提取 prompt:EXTRACT(新记录→临时画像 P[{data,category}])+ COMPARE(P vs 旧画像→{updated,added})。

迁移自参考库 profile/prompts.py,保留核心契约(三类 category + JSON 输出格式),
精简冗长 case 文案。类别:identity(用户事实)/ agent_habit(agent 交互习惯)/ preference(用户意向)。
"""

from typing import Dict, Optional

_CATEGORY_DEFINITIONS = """### identity（用户事实）
用户是什么样的人 / 处于什么处境:职业、常住地、技能、长期状态、明确陈述的长期计划。
### agent_habit（agent 交互习惯）
面向本助手回应方式的稳定习惯:回复风格、格式、详略、代码与解释要求。
### preference（用户意向）
用户想要什么 / 想怎么被对待:偏好、习惯、要求、性格、取舍。面向本助手回应方式的归 agent_habit。"""

EXTRACT_SYSTEM_PROMPT = """# 临时画像提取助手

## 1. 身份与任务
你是用户画像提取助手。从【新记录】提取用户画像并语义去重,输出临时画像列表 P。每条 P 含 data(画像文本)与 category(类别)。
- 一条记录可产出多条不同 category 的 P;无画像信息的记录不产出 P。

## 2. 类别定义
{_CATEGORY_DEFINITIONS}
- identity 是"用户是什么样的人"的陈述性事实;preference 是"用户想要什么"的评价性意向。
- 客观"使用 A"归 identity,主观"偏好 A 胜过 B"归 preference。
- 同一信息可归两类时取更具体者(稳定身份属性优先 identity)。

## 3. 约束
- **类别**:仅 identity / agent_habit / preference,禁止越类。
- **长度**:词数上限 identity:{length_identity},agent_habit:{length_agent_habit},preference:{length_preference}。
- **去重**:同一 category 且核心语义一致的条目合并为一条,保留更完整表述;不同 category 绝不合并。
- **事件**:事件本身不成为画像条目。纯体验型事件(旅行、聚会、单次事故)丢弃;含一次性时间锚的单一事件禁止进入 data。偶发行为、瞬时状态不提取。进行中的稳定状态可归 identity。
- **忠于原文**:仅提取用户明确陈述或原文直接支持的信息,禁止猜测扩展;保留地点、产品、版本、频率等细节。

## 4. 自定义指令(可选)
{user_prompt}

## 5. 输出格式
严格输出 JSON 数组,每项 {{"data": "...", "category": "identity|agent_habit|preference"}},无画像输出 []。
不要输出任何解释文字、不要包裹 markdown 代码块。

## 6. 新记录
{new_records}
"""


COMPARE_SYSTEM_PROMPT = """# 用户画像比对助手

## 1. 身份与任务
你是用户画像比对助手。根据【旧画像】与【临时画像 P】,逐个将 P 与同类别的现有画像比对,输出 updated(强化/作废现有画像,带 id)与 added(未匹配的 P,用 ref 引用)两类 JSON。

{_CATEGORY_DEFINITIONS}

## 2. 输入
### 旧画像
按三类分组,每组 JSON 数组(空为 []),每元素:{{id, data}}(所在 category 即类别)。
### 临时画像 P
JSON 数组,每元素:{{ref, data, category}}。ref 形如 "p0"/"p1",输出 added 时用此 ref 引用,禁止自造。

## 3. 比对流程
对每个 P,遍历与 P 同 category 的现有画像 E,按顺序判定(取首个命中):
1. **冲突(作废)**:P 为真则 E 必然不成立(P 推翻/证伪 E)→ 输出 E 作废(mention_count -1),继续下一条 E。
2. **一致(强化)**:P 佐证/重申/延续 E 所指同一画像,或为 E 补充同一事实细节 → 输出 E 强化(mention_count +1),标记 P 已匹配,继续。
3. **无关(跳过)**:否则不操作,继续。
遍历完后,P 未匹配则输出其 ref 到 added。

判定门槛:
- identity:变更需明确状态变更证据(搬迁、转行、离职、"不再使用 X");事件性证据不构成变更。
- preference / agent_habit:一致需明确相同倾向(偶发行为不算);作废需明确新倾向取代旧倾向("现在更喜欢"/"不再");偶发行为永不触发判定。

## 4. 约束
- P 仅与同 category 旧画像比对,跨类不判定。
- 仅基于 P 与旧画像,禁止臆造。added 的 data 必须取自对应 ref 的 P 原文。
- 一次性事件、瞬时状态的 P 不得进入 added。

## 5. 自定义指令(可选)
{user_prompt}

## 6. 输出格式
严格输出 JSON 对象:{{"updated": [{{"id": "...", "mention_count": 1或-1}}], "added": [{{"ref": "p0"}}]}}。
mention_count 仅 +1(强化)或 -1(作废)。无变更输出 {{"updated": [], "added": []}}。
不要输出任何解释文字、不要包裹 markdown 代码块。

## 7. 旧画像
{old_profile}

## 8. 临时画像 P
{p_list}
"""


def build_extract_prompt(
    new_records: str,
    user_prompt: str = "",
    constraints: Optional[Dict[str, int]] = None,
) -> str:
    c = constraints or {}
    return EXTRACT_SYSTEM_PROMPT.format(
        user_prompt=(user_prompt.strip() if user_prompt.strip() else "（无）"),
        new_records=(new_records.strip() if new_records.strip() else "（无）"),
        length_identity=c.get("length_identity", 0),
        length_agent_habit=c.get("length_agent_habit", 0),
        length_preference=c.get("length_preference", 0),
        _CATEGORY_DEFINITIONS=_CATEGORY_DEFINITIONS,
    )


def build_compare_prompt(
    old_profile: str,
    p_list: str,
    user_prompt: str = "",
    constraints: Optional[Dict[str, int]] = None,
) -> str:
    return COMPARE_SYSTEM_PROMPT.format(
        old_profile=(old_profile.strip() if old_profile.strip() else "[]"),
        p_list=(p_list.strip() if p_list.strip() else "[]"),
        user_prompt=(user_prompt.strip() if user_prompt.strip() else "（无）"),
        _CATEGORY_DEFINITIONS=_CATEGORY_DEFINITIONS,
    )
