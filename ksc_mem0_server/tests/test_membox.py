"""membox 闭环测试:pextract 解析 + BoxSnapshot + MemBoxSealHandler 闭环。mock LLM,真 PG。"""

import json
from unittest.mock import MagicMock

from ksc_mem0_server.digests import (
    BoxSnapshot,
    MemBoxPendingQueue,
    MemBoxSealHandler,
    MemBoxStore,
    ensure_digests_schema,
    parse_pextract_result,
)


def _vec(dim=1536):
    return [0.1] * dim


def test_parse_pextract_result():
    raw = json.dumps({"keywords": ["咖啡", "手冲"], "topic": "讨论咖啡偏好", "explicit_mentions": ["买了咖啡豆"]})
    r = parse_pextract_result(raw)
    assert r["topic"] == "讨论咖啡偏好"
    assert r["keywords"] == ["咖啡", "手冲"]
    assert r["explicit_mentions"] == ["买了咖啡豆"]


def test_parse_pextract_result_empty():
    assert parse_pextract_result("not json") == {}
    assert parse_pextract_result(json.dumps({"keywords": [], "topic": "", "explicit_mentions": []})) == {}


def test_box_snapshot_roundtrip():
    box = BoxSnapshot(owner_type="user", owner_id="u1", user_id="u1", run_id="r1", messages=["hi", "hello"], memory_ids=["m1"])
    d = box.to_dict()
    box2 = BoxSnapshot.from_dict(d)
    assert box2.owner_id == "u1"
    assert box2.messages == ["hi", "hello"]
    assert box2.memory_ids == ["m1"]


def test_membox_seal_handler_loop(product_pool):
    ensure_digests_schema(product_pool)
    store = MemBoxStore(product_pool, dims=1536)
    pending = MemBoxPendingQueue()
    llm = MagicMock()
    llm.generate_response.return_value = json.dumps({"keywords": ["咖啡"], "topic": "咖啡偏好", "explicit_mentions": ["买了豆"]})
    embedder = MagicMock()
    embedder.embed.return_value = _vec()
    handler = MemBoxSealHandler(llm, embedder, store, pending)

    box = BoxSnapshot(owner_type="user", owner_id="u1", user_id="u1", run_id="r1", messages=["I love coffee", "Bought beans"], memory_ids=["m1", "m2"])
    box_id = handler.handle("u1", box.to_dict())

    assert box_id is not None
    got = store.get(box_id)
    assert got is not None
    assert got["payload"]["topic"] == "咖啡偏好"
    assert "咖啡" in got["payload"]["content"]
    assert got["payload"]["memory_ids"] == ["m1", "m2"]
    # pending_queue push(user)
    assert pending.has_pending("user", "u1")
    assert pending.drain("user", "u1") == [box_id]


def test_membox_seal_handler_empty_box(product_pool):
    ensure_digests_schema(product_pool)
    store = MemBoxStore(product_pool, dims=1536)
    handler = MemBoxSealHandler(MagicMock(), MagicMock(), store)
    box = BoxSnapshot(owner_type="user", owner_id="u1", user_id="u1", messages=[])
    assert handler.handle("u1", box.to_dict()) is None


def test_membox_seal_handler_fallback_content(product_pool):
    ensure_digests_schema(product_pool)
    store = MemBoxStore(product_pool, dims=1536)
    llm = MagicMock()
    llm.generate_response.return_value = "not json"  # pextract 解析失败
    embedder = MagicMock()
    embedder.embed.return_value = _vec()
    handler = MemBoxSealHandler(llm, embedder, store)

    box = BoxSnapshot(owner_type="user", owner_id="u1", user_id="u1", messages=["讨论了天气和咖啡"])
    box_id = handler.handle("u1", box.to_dict())
    assert box_id is not None
    got = store.get(box_id)
    # pextract 失败 → fallback content 取 dialog 头部
    assert "讨论了天气" in got["payload"]["content"]
    assert got["payload"]["topic"] == ""


def test_pending_queue_dedup():
    q = MemBoxPendingQueue()
    assert q.push("b1", "user", "u1") is True
    assert q.push("b1", "user", "u1") is False  # 去重
    assert q.push("b2", "user", "u1") is True
    drained = q.drain("user", "u1")
    assert set(drained) == {"b1", "b2"}
    assert not q.has_pending("user", "u1")
