"""digests 存储层测试:MemBoxStore + MemSceneStore + settings + feedback。真 PG。"""

import uuid

from ksc_mem0_server.digests import (
    DigestsFeedbackStore,
    DigestsSettingsStore,
    MemBoxStore,
    MemSceneStore,
    ensure_digests_schema,
    is_builtin_scene,
    version_num,
)


def _vec(dim=1536):
    return [0.1] * dim


def test_is_builtin_scene():
    assert is_builtin_scene("all") is True
    assert is_builtin_scene("hour:2026-01-01") is True
    assert is_builtin_scene("range:7d") is True
    assert is_builtin_scene("some-uuid") is False
    assert is_builtin_scene("") is True


def test_version_num():
    assert version_num("3") == 3
    assert version_num("V003") == 3
    assert version_num("") == 0
    assert version_num("abc") == 0


def test_membox_store_upsert_search(product_pool):
    ensure_digests_schema(product_pool)
    store = MemBoxStore(product_pool, dims=1536)
    bid = str(uuid.uuid4())
    store.upsert(
        [bid], [_vec()],
        [{"user_id": "u1", "run_id": "r1", "topic": "咖啡偏好", "content": "讨论手冲咖啡", "memory_ids": ["m1"]}],
    )
    got = store.get(bid)
    assert got is not None
    assert got["payload"]["topic"] == "咖啡偏好"
    results = store.search(_vec(), filters={"user_id": "u1", "run_id": "r1"}, limit=5)
    assert len(results) == 1
    assert results[0]["payload"]["topic"] == "咖啡偏好"


def test_membox_store_requires_scope(product_pool):
    ensure_digests_schema(product_pool)
    store = MemBoxStore(product_pool, dims=1536)
    try:
        store.search(_vec(), filters={}, limit=5)
        assert False
    except ValueError:
        pass


def test_membox_store_count_delete(product_pool):
    ensure_digests_schema(product_pool)
    store = MemBoxStore(product_pool, dims=1536)
    store.upsert(["a", "b"], [_vec(), _vec()], [{"user_id": "u1"}, {"user_id": "u1"}])
    assert store.count({"user_id": "u1"}) == 2
    store.delete(["a"])
    assert store.count({"user_id": "u1"}) == 1


def test_memscene_store_upsert_get(product_pool):
    ensure_digests_schema(product_pool)
    store = MemSceneStore(product_pool, dims=1536)
    rid = str(uuid.uuid4())
    store.upsert(
        [rid], [_vec()],
        [{"owner_id": "u1", "owner_type": "user", "scene_id": "all", "version_id": "1", "scene_name": "咖啡", "content": "咖啡偏好汇总", "status": "COMPLETED"}],
    )
    got = store.get(rid)
    assert got is not None
    assert got["payload"]["scene_id"] == "all"
    assert got["payload"]["status"] == "COMPLETED"


def test_memscene_store_list_overviews(product_pool):
    ensure_digests_schema(product_pool)
    store = MemSceneStore(product_pool, dims=1536)
    store.upsert(["o1", "o2"], [_vec(), _vec()], [
        {"owner_id": "u1", "owner_type": "user", "scene_id": "all", "version_id": "1", "content": "v1", "status": "COMPLETED"},
        {"owner_id": "u1", "owner_type": "user", "scene_id": "all", "version_id": "2", "content": "v2", "status": "COMPLETED"},
    ])
    overviews = store.list_overviews("user", "u1")
    assert len(overviews) == 2


def test_memscene_store_search_requires_owner(product_pool):
    ensure_digests_schema(product_pool)
    store = MemSceneStore(product_pool, dims=1536)
    try:
        store.search(_vec(), filters={}, limit=5)
        assert False
    except ValueError:
        pass


def test_digests_settings_store(product_pool):
    ensure_digests_schema(product_pool)
    s = DigestsSettingsStore(product_pool, "p1")
    assert s.get("user", "u1") is None
    s.upsert("user", "u1", memory_range="LAST_3_MONTHS", schedule_time=["09:00"])
    cfg = s.get("user", "u1")
    assert cfg["memory_range"] == "LAST_3_MONTHS"
    assert cfg["schedule_time"] == ["09:00"]
    assert cfg["digest_version_chain"] == []
    s.update_version_chain("user", "u1", ["v2-id", "v1-id"])
    assert s.get_version_chain("user", "u1") == ["v2-id", "v1-id"]


def test_digests_feedback_store(product_pool):
    ensure_digests_schema(product_pool)
    f = DigestsFeedbackStore(product_pool, "p1")
    fid = f.create("user", "u1", "希望突出咖啡相关内容")
    items = f.list("user", "u1")
    assert len(items) == 1
    assert items[0]["content"] == "希望突出咖啡相关内容"
    assert f.delete(fid) is True
    assert f.list("user", "u1") == []
