"""scene_search:场景检索(query → memscene 语义检索 + membox 反查)。

迁移自参考库 digests/scene_search.py,简化:仅 memscene 语义检索 + 按 scene_id 聚合最新版本。
membox 反查(通过 user_scene_ids)留 P5(依赖 attach 回写)。
"""

import logging
from typing import Any, Dict, List

from .constants import is_builtin_scene, version_num
from .vector_store import MemSceneStore

logger = logging.getLogger(__name__)


def scene_search(
    query_vector: List[float],
    memscene_store: MemSceneStore,
    owner_type: str,
    owner_id: str,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """场景语义检索:按 owner 过滤的 memscene 向量检索,过滤内置 scene_id,按 scene_id 聚合最新版本。"""
    results = memscene_store.search(
        query_vector,
        filters={"owner_id": owner_id, "owner_type": owner_type},
        limit=limit * 2,
    )
    # 按 scene_id 去重取最新 version_id,过滤内置
    by_scene: Dict[str, Dict[str, Any]] = {}
    for r in results:
        payload = r.get("payload") or {}
        sid = payload.get("scene_id")
        if not sid or is_builtin_scene(sid):
            continue
        existing = by_scene.get(sid)
        if existing is None or version_num(payload.get("version_id", "")) > version_num(existing["payload"].get("version_id", "")):
            by_scene[sid] = r
    return list(by_scene.values())[:limit]
