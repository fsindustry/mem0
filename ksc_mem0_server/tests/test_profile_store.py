"""画像存储层测试:ProfileStore(pgvector)+ ProfileStrategyStore + ProfileTaskStore。真 PG。"""

import uuid

from ksc_mem0_server.profile import (
    ProfileStore,
    ProfileStrategyStore,
    ProfileTaskStore,
    ensure_profile_schema,
)


def _vec(dim=1536):
    return [0.1] * dim


def test_profile_store_upsert_search(product_pool):
    store = ProfileStore(product_pool, dims=1536)
    pid = str(uuid.uuid4())
    store.upsert(
        [pid],
        [_vec()],
        [{"user_id": "u1", "data": "用户是工程师", "category": "identity", "strategy_id": "s1", "version": 1}],
    )
    got = store.get(pid)
    assert got is not None
    assert got["payload"]["user_id"] == "u1"
    assert got["payload"]["data"] == "用户是工程师"

    results = store.search(_vec(), filters={"user_id": "u1"}, limit=5)
    assert len(results) == 1
    assert results[0]["id"] == pid
    assert results[0]["payload"]["category"] == "identity"


def test_profile_store_count_delete(product_pool):
    store = ProfileStore(product_pool, dims=1536)
    store.upsert(
        ["a", "b"],
        [_vec(), _vec()],
        [{"user_id": "u1", "strategy_id": "s1"}, {"user_id": "u1", "strategy_id": "s2"}],
    )
    assert store.count({"user_id": "u1"}) == 2
    assert store.count({"strategy_id": "s1"}) == 1
    store.delete_by_strategy("s1")
    assert store.count({"strategy_id": "s1"}) == 0
    assert store.count({"user_id": "u1"}) == 1


def test_profile_strategy_store_version_increments(product_pool):
    ensure_profile_schema(product_pool)
    st = ProfileStrategyStore(product_pool, "p1")
    v1 = st.record_profile_update("u1", "s1")
    v2 = st.record_profile_update("u1", "s1")
    assert v2 == v1 + 1
    assert st.get_profile_version("u1", "s1") == v2
    versions = st.get_user_profile_versions("u1")
    assert versions["s1"] == v2


def test_profile_task_store_claim_publish_flow(product_pool):
    ensure_profile_schema(product_pool)
    ts = ProfileTaskStore(product_pool, "p1")
    # 登记并就绪一个事件
    eid = ts.register("u1", "s1", "op1", {"text": "我喜欢咖啡"}, ready=True)
    assert eid

    due = ts.due()
    assert len(due) == 1
    assert due[0]["user_id"] == "u1"
    assert due[0]["requested_generation"] == 1

    run = ts.claim("u1", "s1")
    assert run is not None
    assert run["target_generation"] == 1
    assert run["from_generation"] == 0
    assert run["inputs"] == [{"text": "我喜欢咖啡"}]

    ts.publish(run, snapshot_id="snap-1")
    assert ts.is_published(run["review_id"]) is True

    # 发布后 processed_generation 推进,不再 due
    assert ts.due() == []


def test_profile_task_store_fail_releases_lease(product_pool):
    ensure_profile_schema(product_pool)
    ts = ProfileTaskStore(product_pool, "p1")
    ts.register("u2", "s2", "op1", {"text": "x"}, ready=True)
    run = ts.claim("u2", "s2")
    assert run is not None
    ts.fail(run, error="boom")
    # 失败后租约释放,可再次 due(retry_count=1 < limit)
    due = ts.due()
    assert len(due) == 1
    assert due[0]["retry_count"] == 1
