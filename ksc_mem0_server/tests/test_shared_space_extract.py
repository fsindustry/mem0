"""P4c 批次抽取闭环测试:parse_extract_actions + SharedSpaceExtractHandler。mock LLM,真 PG。"""

import json
from unittest.mock import MagicMock

from ksc_mem0_server.shared_space import (
    InFlightGate,
    SharedSpaceExtractHandler,
    SharedSpaceStore,
    SharedVectorStore,
    ensure_shared_space_schema,
    parse_extract_actions,
)


def _vec(dim=1536):
    return [0.1] * dim


def test_parse_extract_actions_create():
    raw = json.dumps({"actions": [{"type": "CREATE", "content": "团队约定用 Go 1.21"}, {"type": "CREATE", "content": "API 风格 RESTful"}]})
    actions = parse_extract_actions(raw)
    assert len(actions) == 2
    assert actions[0]["content"] == "团队约定用 Go 1.21"


def test_parse_extract_actions_empty():
    assert parse_extract_actions(json.dumps({"actions": []})) == []


def test_parse_extract_actions_rejects_bad():
    import pytest

    with pytest.raises(ValueError):
        parse_extract_actions(json.dumps({"actions": [{"type": "BOGUS", "content": "x"}]}))
    with pytest.raises(ValueError):
        parse_extract_actions(json.dumps({"actions": [{"type": "CREATE", "content": ""}]}))
    with pytest.raises(ValueError):
        parse_extract_actions(json.dumps({"foo": "bar"}))


def test_extract_handler_loop(product_pool):
    ensure_shared_space_schema(product_pool)
    store = SharedSpaceStore(product_pool, "p1")
    vs = SharedVectorStore(product_pool, dims=1536)
    gate = InFlightGate()
    space = store.create_space("s1", extract_enabled=True, token_threshold=100)
    store.add_members(space["space_id"], [("u1", "user")])
    # 登记两条 pending(直接写 store,绕过 registrar)
    store.register_pending(space["space_id"], "f1", "v1", 50, None, "u1", "")
    store.register_pending(space["space_id"], "f2", "v2", 50, None, "u1", "")
    store.adjust_pending_tokens(space["space_id"], 100)

    llm = MagicMock()
    llm.generate_response.return_value = json.dumps({"actions": [{"type": "CREATE", "content": "用户偏好 Go 与手冲咖啡"}]})
    embedder = MagicMock()
    embedder.embed.return_value = _vec()
    handler = SharedSpaceExtractHandler(llm, embedder, store, vs, gate, batch_fact_count=10)

    # 模拟队列调度:gate acquire + lease,调 handle
    token = "tok1"
    store.try_acquire_run_lease(space["space_id"], token, 300)
    gate.try_acquire(space["space_id"])
    handler.handle(space["space_id"], {"space_id": space["space_id"], "lease_token": token})

    # 共享记忆已写入 pgvector
    assert vs.count(space["space_id"]) == 1
    items = vs.list(space["space_id"])
    assert items[0]["payload"]["content"] == "用户偏好 Go 与手冲咖啡"

    # pending 已 finish(无候选)
    assert store.list_pending_candidates(space["space_id"], 10) == []
    # pending_tokens 清减(消费 100)
    assert store.get_space(space["space_id"])["pending_tokens"] == 0
    # gate + lease 已释放
    assert not gate.is_inflight(space["space_id"])
    assert store.get_space(space["space_id"])["run_lease_token"] is None


def test_extract_handler_disabled_space(product_pool):
    ensure_shared_space_schema(product_pool)
    store = SharedSpaceStore(product_pool, "p1")
    vs = SharedVectorStore(product_pool, dims=1536)
    gate = InFlightGate()
    space = store.create_space("s1", extract_enabled=False)
    store.register_pending(space["space_id"], "f1", "v1", 10, None, "u1", "")
    handler = SharedSpaceExtractHandler(MagicMock(), MagicMock(), store, vs, gate)
    handler.handle(space["space_id"], {"space_id": space["space_id"]})
    # 未启用 → 不抽取,pending 仍在
    assert len(store.list_pending_candidates(space["space_id"], 10)) == 1
    assert vs.count(space["space_id"]) == 0


def test_extract_handler_llm_fail_releases_pending(product_pool):
    ensure_shared_space_schema(product_pool)
    store = SharedSpaceStore(product_pool, "p1")
    vs = SharedVectorStore(product_pool, dims=1536)
    gate = InFlightGate()
    space = store.create_space("s1", extract_enabled=True)
    store.register_pending(space["space_id"], "f1", "v1", 10, None, "u1", "")
    store.adjust_pending_tokens(space["space_id"], 10)

    llm = MagicMock()
    llm.generate_response.return_value = "not json"  # 解析失败
    embedder = MagicMock()
    embedder.embed.return_value = _vec()
    handler = SharedSpaceExtractHandler(llm, embedder, store, vs, gate)

    token = "tok1"
    store.try_acquire_run_lease(space["space_id"], token, 300)
    gate.try_acquire(space["space_id"])
    handler.handle(space["space_id"], {"space_id": space["space_id"], "lease_token": token})

    # LLM 失败 → pending 回退(仍可再抽取),无共享记忆
    assert len(store.list_pending_candidates(space["space_id"], 10)) == 1
    assert vs.count(space["space_id"]) == 0
    assert not gate.is_inflight(space["space_id"])
