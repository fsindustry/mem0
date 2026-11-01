"""digests 公共常量与纯函数。

迁移自参考库 digests/constants.py,去 build_table_name(分库后表名无 _{pid},无需拼)。
"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone

# 内置 scene_id 前缀(all/hour:/day:/range:)非真实场景,分类/删除时过滤。
BUILTIN_SCENE_PREFIXES = ("all", "hour:", "day:", "range:")

_VERSION_NUM_RE = re.compile(r"\d+")

WEEKDAYS = ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY")


def is_builtin_scene(scene_id: str) -> bool:
    """判断 scene_id 是否为内置聚合记录(all/hour:/day:/range:),非真实场景。"""
    if not scene_id:
        return True
    return any(scene_id.startswith(p) for p in BUILTIN_SCENE_PREFIXES)


def today_weekday() -> str:
    return WEEKDAYS[datetime.now().astimezone().weekday()]


def version_num(version_id: str) -> int:
    """从 version_id(如 "3",兼容遗留 "V003")提取数字。空/无数字返回 0。"""
    if not version_id:
        return 0
    m = _VERSION_NUM_RE.search(version_id)
    return int(m.group()) if m else 0


def now_iso() -> str:
    """当前 UTC 时间 ISO 字符串。"""
    return datetime.now(timezone.utc).isoformat()


def owner_keys(owner_type: str) -> tuple[str, str]:
    """owner_type → (主记忆归属键, membox 场景反查键)。"""
    if owner_type == "user":
        return "user_id", "user_scene_ids"
    return "agent_id", "agent_scene_ids"


@dataclass
class Episode:
    """未 seal 的 episode 条目(切段缓冲/分段共用)。

    字段:(ep_id, data, created_at, updated_at, group_id)
      - ep_id: episode 主键(主记忆 vector_store 的 id)
      - data: episode 文本(切段 LLM 输入、seal 后拼 dialog)
      - created_at/updated_at: ISO 时间戳
      - group_id: 上轮切段分配的段标识;None 表示本轮新增未分组
    """

    ep_id: str
    data: str
    created_at: str
    updated_at: str
    group_id: str | None = None
