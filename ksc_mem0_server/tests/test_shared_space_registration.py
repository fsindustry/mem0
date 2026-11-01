"""P4b 登记与调度测试:SharedSpaceRegistrar + InFlightGate + make_trigger + publisher。真 PG。"""

from unittest.mock import MagicMock

from ksc_mem0_server.shared_space import (
    InFlightGate,
    SharedSpacePublisher,
    SharedSpaceRegistrar,
    SharedSpaceStore,
    compute_source_version,
    ensure_shared_space_schema,
    make_trigger,
)
from ksc_mem0_server.tasks.queue import GlobalTaskQueue


def test_compute_source_version_stable():
    v1 = compute_source_version("text", user_id="u1", created_at="2026-01-01")
    v2 = compute_source_version("text", user_id="u1", created_at="2026-01-01")
    assert v1 == v2
    # 不同身份不同 version
    assert compute_source_version("text", user_id="u1") != compute_source_version("text", user_id="u2")


def test_inflight_gate_mutual_exclusion():
    gate = InFlightGate()
    assert gate.try_acquire("s1") is True
    assert gate.try_acquire("s1") is False  # 互斥
    gate.release("s1")
    assert gate.try_acquire("s1") is True


def test_registrar_registers_and_triggers(product_pool):
    ensure_shared_space_schema(product_pool)
    store = SharedSpaceStore(product_pool, "p1")
    space = store.create_space("s1", extract_enabled=True, token_threshold=100)
    store.add_members(space["space_id"], [("u1", "user")])

    triggered: list = []
    memory_provider = MagicMock()
    memory_provider().config.shared_space_token_threshold = 100
    memory_provider().config.shared_space_token_estimator = "char_weighted"
    trigger = lambda sid, source="threshold": triggered.append(sid) or True  # noqa: E731
    reg = SharedSpaceRegistrar(store, memory_provider, trigger)

    # 登记一条 fact(token 估算 >0,累积达阈值 100 需多条;此处阈值设小)
    # 用 char 模式:中文每字符 1 token
    reg.on_facts_persisted([("f1", "咖啡偏好手冲意式浓缩咖啡豆研磨度粗细", None, {"user_id": "u1", "created_at": "2026-01-01T00:00:00Z"})])

    # pending 已登记
    cands = store.list_pending_candidates(space["space_id"], 10)
    assert len(cands) == 1
    assert cands[0]["fact_id"] == "f1"
    # pending_tokens 已累计
    assert store.get_space(space["space_id"])["pending_tokens"] > 0


def test_registrar_skips_disabled_space(product_pool):
    ensure_shared_space_schema(product_pool)
    store = SharedSpaceStore(product_pool, "p1")
    space = store.create_space("s1", extract_enabled=False)  # 未启用
    store.add_members(space["space_id"], [("u1", "user")])
    reg = SharedSpaceRegistrar(store, MagicMock(), lambda sid, source="threshold": True)
    reg.on_facts_persisted([("f1", "text", None, {"user_id": "u1"})])
    assert store.list_pending_candidates(space["space_id"], 10) == []


def test_registrar_no_matching_space(product_pool):
    ensure_shared_space_schema(product_pool)
    store = SharedSpaceStore(product_pool, "p1")
    reg = SharedSpaceRegistrar(store, MagicMock(), lambda sid, source="threshold": True)
    # 无空间,无成员匹配
    reg.on_facts_persisted([("f1", "text", None, {"user_id": "nobody"})])
    # 快速短路:list_enabled_spaces 为空
    assert store.list_enabled_spaces() == []


def test_registrar_obsolete_on_delete(product_pool):
    ensure_shared_space_schema(product_pool)
    store = SharedSpaceStore(product_pool, "p1")
    space = store.create_space("s1", extract_enabled=True, token_threshold=100)
    store.add_members(space["space_id"], [("u1", "user")])
    reg = SharedSpaceRegistrar(store, MagicMock(), None)
    reg.on_facts_persisted([("f1", "咖啡", None, {"user_id": "u1", "created_at": "2026-01-01T00:00:00Z"})])
    assert store.get_space(space["space_id"])["pending_tokens"] > 0
    reg.on_fact_deleted("f1")
    assert store.get_space(space["space_id"])["pending_tokens"] == 0


def test_make_trigger_enqueues(product_pool):
    ensure_shared_space_schema(product_pool)
    store = SharedSpaceStore(product_pool, "p1")
    space = store.create_space("s1", extract_enabled=True)
    gate = InFlightGate()
    queue = GlobalTaskQueue(maxsize=10)
    trigger = make_trigger(lambda: queue, gate, store, "p1")

    ok = trigger(space["space_id"], source="threshold")
    assert ok is True
    item = queue.get(timeout=1)
    assert item is not None
    assert item.category == "shared_space"
    assert item.payload["space_id"] == space["space_id"]
    # gate 仍持有(handler finally release)
    assert gate.is_inflight(space["space_id"])
    # 二次触发 gate 互斥
    assert trigger(space["space_id"]) is False
    # release 后可再触发
    gate.release(space["space_id"])
    store.release_run_lease(space["space_id"], item.payload["lease_token"])


def test_publisher_scans_enabled(product_pool):
    ensure_shared_space_schema(product_pool)
    store = SharedSpaceStore(product_pool, "p1")
    space = store.create_space("s1", extract_enabled=True)
    store.adjust_pending_tokens(space["space_id"], 500)  # 有积压
    gate = InFlightGate()
    queue = GlobalTaskQueue(maxsize=10)
    trigger = make_trigger(lambda: queue, gate, store, "p1")
    publisher = SharedSpacePublisher(store, trigger, interval=60)
    n = publisher.publish_round()
    assert n == 1
    assert queue.get(timeout=1) is not None
