"""shared_space 存储层测试:SharedSpaceStore + SharedVectorStore + MemberCatalogStore。真 PG。"""

import uuid
from datetime import datetime, timezone

from ksc_mem0_server.shared_space import (
    DuplicateSpaceNameError,
    MemberCatalogStore,
    SharedSpaceStore,
    SharedVectorStore,
    ensure_shared_space_schema,
    estimate_tokens,
    normalize_member_type,
)


def _vec(dim=1536):
    return [0.1] * dim


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abc") == 1  # 3*0.3=0.9 round=1
    assert estimate_tokens("你好") == 2  # CJK 1.0*2
    assert estimate_tokens("你好", mode="char") == 2


def test_normalize_member_type():
    assert normalize_member_type("USER") == "user"
    assert normalize_member_type("agent") == "agent"
    assert normalize_member_type("bogus") is None
    assert normalize_member_type("") is None


def test_space_crud(product_pool):
    ensure_shared_space_schema(product_pool)
    store = SharedSpaceStore(product_pool, "p1")
    space = store.create_space("my-space", extract_enabled=True, extract_prompt="提取", token_threshold=2000)
    assert space["name"] == "my-space"
    assert space["extract_enabled"] is True
    assert space["token_threshold"] == 2000

    got = store.get_space(space["space_id"])
    assert got["name"] == "my-space"

    total, spaces = store.list_spaces()
    assert total == 1 and spaces[0]["space_id"] == space["space_id"]

    assert store.update_name(space["space_id"], "new-name")
    assert store.get_space(space["space_id"])["name"] == "new-name"

    assert store.set_enabled(space["space_id"], False)
    assert store.get_space(space["space_id"])["extract_enabled"] is False

    assert store.delete_space(space["space_id"]) is True
    assert store.get_space(space["space_id"]) is None


def test_space_duplicate_name(product_pool):
    ensure_shared_space_schema(product_pool)
    store = SharedSpaceStore(product_pool, "p1")
    store.create_space("dup")
    try:
        store.create_space("dup")
        assert False
    except DuplicateSpaceNameError:
        pass


def test_rules_crud(product_pool):
    ensure_shared_space_schema(product_pool)
    store = SharedSpaceStore(product_pool, "p1")
    space = store.create_space("s1")
    rule = store.add_rule(space["space_id"], "只提取咖啡相关")
    assert rule["content"] == "只提取咖啡相关"
    assert store.update_rule(space["space_id"], rule["rule_id"], "新规则")
    assert store.get_rule(space["space_id"], rule["rule_id"])["content"] == "新规则"
    total, rules = store.list_rules(space["space_id"])
    assert total == 1
    assert store.delete_rule(space["space_id"], rule["rule_id"]) is True
    assert store.list_rules(space["space_id"])[0] == 0


def test_members_crud_and_reverse(product_pool):
    ensure_shared_space_schema(product_pool)
    store = SharedSpaceStore(product_pool, "p1")
    space = store.create_space("s1")
    added = store.add_members(space["space_id"], [("u1", "user"), ("a1", "agent")])
    assert len(added) == 2
    # 重复添加幂等
    assert store.add_members(space["space_id"], [("u1", "user")]) == []

    total, members = store.list_members(space["space_id"])
    assert total == 2
    # 成员反查空间
    assert store.list_member_space_ids("u1", "user") == [space["space_id"]]

    assert store.remove_members(space["space_id"], [("u1", "user")]) == 1
    assert store.list_member_space_ids("u1", "user") == []


def test_pending_register_claim_finish(product_pool):
    ensure_shared_space_schema(product_pool)
    store = SharedSpaceStore(product_pool, "p1")
    space = store.create_space("s1")
    now = datetime.now(timezone.utc)
    # 幂等登记
    assert store.register_pending(space["space_id"], "f1", "v1", 100, now, "u1", "") is True
    assert store.register_pending(space["space_id"], "f1", "v1", 100, now, "u1", "") is False

    cands = store.list_pending_candidates(space["space_id"], 10)
    assert len(cands) == 1 and cands[0]["fact_id"] == "f1"

    store.adjust_pending_tokens(space["space_id"], 100)
    assert store.get_space(space["space_id"])["pending_tokens"] == 100

    claimed = store.claim_pending(space["space_id"], ["f1"], "batch-1")
    assert claimed == ["f1"]
    # 已 processing,不再候选
    assert store.list_pending_candidates(space["space_id"], 10) == []

    assert store.finish_pending(space["space_id"], ["f1"], ["v1"]) == 1


def test_pending_obsolete_releases_tokens(product_pool):
    ensure_shared_space_schema(product_pool)
    store = SharedSpaceStore(product_pool, "p1")
    space = store.create_space("s1")
    store.register_pending(space["space_id"], "f1", "v1", 50, None, "u1", "")
    store.adjust_pending_tokens(space["space_id"], 50)
    reclaimed = store.obsolete_pending("f1")
    assert reclaimed == [(space["space_id"], 50)]
    store.adjust_pending_tokens(space["space_id"], -50)
    assert store.get_space(space["space_id"])["pending_tokens"] == 0


def test_batches(product_pool):
    ensure_shared_space_schema(product_pool)
    store = SharedSpaceStore(product_pool, "p1")
    space = store.create_space("s1")
    assert store.create_batch(space["space_id"], "b1") is True
    assert store.update_batch("b1", {"rounds": 2, "status": "done"})
    batches = store.list_batches_by_status(space["space_id"], "done")
    assert len(batches) == 1 and batches[0]["rounds"] == 2


def test_run_lease(product_pool):
    ensure_shared_space_schema(product_pool)
    store = SharedSpaceStore(product_pool, "p1")
    space = store.create_space("s1")
    assert store.try_acquire_run_lease(space["space_id"], "tok1", 60) is True
    # 二次获取(未释放)失败
    assert store.try_acquire_run_lease(space["space_id"], "tok2", 60) is False
    assert store.release_run_lease(space["space_id"], "tok1") is True
    assert store.try_acquire_run_lease(space["space_id"], "tok2", 60) is True


def test_shared_vector_store(product_pool):
    ensure_shared_space_schema(product_pool)
    store = SharedVectorStore(product_pool, dims=1536)
    sid = str(uuid.uuid4())
    store.upsert(["m1"], [_vec()], [{"space_id": sid, "data": "共享记忆内容", "user_id": "u1"}])
    got = store.get("m1")
    assert got["payload"]["data"] == "共享记忆内容"
    results = store.search(_vec(), space_id=sid, limit=5)
    assert len(results) == 1
    assert store.count(sid) == 1
    assert store.delete_by_space(sid) == 1
    assert store.count(sid) == 0


def test_member_catalog(product_pool):
    ensure_shared_space_schema(product_pool)
    store = SharedSpaceStore(product_pool, "p1")
    catalog = MemberCatalogStore(product_pool)
    space = store.create_space("s1")
    store.add_members(space["space_id"], [("u1", "user")])
    spaces = catalog.list_member_spaces("u1", "user")
    assert len(spaces) == 1 and spaces[0]["name"] == "s1"
    members = catalog.list_space_member_ids(space["space_id"])
    assert members == [{"member_id": "u1", "member_type": "user"}]
