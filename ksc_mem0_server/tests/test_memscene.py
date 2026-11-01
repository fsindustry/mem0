"""memscene 聚合测试:scene_assign 解析 + DigestOverallHandler 闭环。mock LLM,真 PG。"""

import json
from unittest.mock import MagicMock

from ksc_mem0_server.digests import (
    DigestOverallHandler,
    MemBoxPendingQueue,
    MemBoxStore,
    MemSceneStore,
    ensure_digests_schema,
    parse_scene_assign_result,
    scene_search,
)
from ksc_mem0_server.digests.settings_store import DigestsSettingsStore


def _vec(dim=1536):
    return [0.1] * dim


def test_parse_scene_assign_existing():
    raw = json.dumps({"scene_ids": ["s1"], "new_scene_name": ""})
    r = parse_scene_assign_result(raw)
    assert r["scene_ids"] == ["s1"]
    assert r["new_scene_name"] == ""


def test_parse_scene_assign_new():
    raw = json.dumps({"scene_ids": ["NEW"], "new_scene_name": "咖啡偏好"})
    r = parse_scene_assign_result(raw)
    assert "NEW" in r["scene_ids"]
    assert r["new_scene_name"] == "咖啡偏好"


def test_overall_aggregate_loop(product_pool):
    ensure_digests_schema(product_pool)
    membox = MemBoxStore(product_pool, dims=1536)
    memscene = MemSceneStore(product_pool, dims=1536)
    settings = DigestsSettingsStore(product_pool, "p1")
    pending = MemBoxPendingQueue()

    # 预置两条 membox(drain 来源)
    membox.upsert(["b1", "b2"], [_vec(), _vec()], [
        {"user_id": "u1", "run_id": "r1", "topic": "咖啡", "content": "讨论手冲咖啡", "memory_ids": []},
        {"user_id": "u1", "run_id": "r1", "topic": "咖啡豆", "content": "买了咖啡豆", "memory_ids": []},
    ])
    pending.push("b1", "user", "u1")
    pending.push("b2", "user", "u1")

    # mock LLM:每次 assign 新建场景,merge 输出合并文本,overview 输出总览
    # threshold=1:每个 membox 独立合并(各建一场景),最后 overview 合并所有场景
    responses_assign = [json.dumps({"scene_ids": ["NEW"], "new_scene_name": "咖啡"})] * 2
    responses_merge = ["咖啡场景合并摘要"] * 4  # 2 场景合并 + 重试余量
    responses_overview = ["咖啡总览:用户关注咖啡偏好"]
    llm = MagicMock()
    llm.generate_response.side_effect = (responses_assign + responses_merge + responses_overview) * 3
    embedder = MagicMock()
    embedder.embed.return_value = _vec()
    handler = DigestOverallHandler(llm, embedder, membox, memscene, settings, pending, merge_threshold=1)

    handler.aggregate("user", "u1")

    # memscene 有 2 场景记录 + 1 all 总览
    scenes = memscene.list(filters={"owner_id": "u1", "owner_type": "user"}, limit=20)
    all_scenes = [s for s in scenes if s["payload"].get("scene_id") == "all"]
    assert len(all_scenes) == 1
    assert all_scenes[0]["payload"]["version_id"] == "1"  # versioned
    # 版本链含 all 总览 record_id
    chain = settings.get_version_chain("user", "u1")
    assert len(chain) == 1
    # pending 已 drain
    assert not pending.has_pending("user", "u1")


def test_overall_aggregate_empty_pending(product_pool):
    ensure_digests_schema(product_pool)
    membox = MemBoxStore(product_pool, dims=1536)
    memscene = MemSceneStore(product_pool, dims=1536)
    settings = DigestsSettingsStore(product_pool, "p1")
    pending = MemBoxPendingQueue()
    handler = DigestOverallHandler(MagicMock(), MagicMock(), membox, memscene, settings, pending)
    handler.aggregate("user", "u1")  # 无 pending,no-op
    assert memscene.count(filters={"owner_id": "u1", "owner_type": "user"}) == 0


def test_scene_search(product_pool):
    ensure_digests_schema(product_pool)
    memscene = MemSceneStore(product_pool, dims=1536)
    memscene.upsert(["s1", "s2"], [_vec(), _vec()], [
        {"owner_id": "u1", "owner_type": "user", "scene_id": "scene-a", "version_id": "1", "scene_name": "咖啡", "content": "咖啡摘要"},
        {"owner_id": "u1", "owner_type": "user", "scene_id": "all", "version_id": "1", "scene_name": "总览", "content": "总览"},
    ])
    results = scene_search(_vec(), memscene, "user", "u1", limit=5)
    # 过滤内置 all,只剩 scene-a
    scene_ids = [r["payload"].get("scene_id") for r in results]
    assert "all" not in scene_ids
    assert "scene-a" in scene_ids
