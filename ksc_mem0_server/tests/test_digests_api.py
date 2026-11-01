"""digests API + daemon + GC 测试。"""

import json
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from ksc_mem0_server.app import create_app
from ksc_mem0_server.digests import (
    DigestsPublisher,
    MemBoxPendingQueue,
    MemSceneGC,
    MemBoxStore,
    MemSceneStore,
    ensure_digests_schema,
)


def _vec(dim=1536):
    return [0.1] * dim


def test_digests_api_seal_and_list(pg_settings, mock_mem0_factories):
    """API:seal membox → list memboxes。需真 ProductRuntime(mock mem0 factory)。"""
    from ksc_mem0_server.runtime import get_registry, reset_registry

    reset_registry()
    reg = get_registry()
    rt = reg.get_or_create("p1")
    # mock facts 的 llm/embedder(seal 用)
    rt.facts.llm = MagicMock()
    rt.facts.llm.generate_response.return_value = json.dumps({"keywords": ["咖啡"], "topic": "咖啡", "explicit_mentions": []})
    rt.facts.embedding_model = MagicMock()
    rt.facts.embedding_model.embed.return_value = _vec()
    # 重建 seal handler 用 mock
    from ksc_mem0_server.digests import MemBoxSealHandler

    rt.membox_seal_handler = MemBoxSealHandler(rt.facts.llm, rt.facts.embedding_model, rt.membox_store, rt.membox_pending_queue)

    app = create_app()
    with TestClient(app) as client:
        headers = {"X-Product-Id": "p1"}
        r = client.post(
            "/v1/digests/seal",
            json={"owner_type": "user", "owner_id": "u1", "user_id": "u1", "run_id": "r1", "messages": ["I love coffee"]},
            headers=headers,
        )
        assert r.status_code == 200
        box_id = r.json()["box_id"]

        r = client.get("/v1/digests/memboxes?user_id=u1", headers=headers)
        assert r.status_code == 200
        items = r.json()["results"]
        assert any(i["id"] == box_id for i in items)

        # feedback
        r = client.post("/v1/digests/feedback?owner_type=user&owner_id=u1", json={"content": "突出咖啡"}, headers=headers)
        assert r.status_code == 200
        r = client.get("/v1/digests/feedback?owner_type=user&owner_id=u1", headers=headers)
        assert len(r.json()["results"]) == 1

        # settings
        r = client.put("/v1/digests/settings?owner_type=user&owner_id=u1", json={"memory_range": "LAST_1_DAY", "schedule_time": ["09:00"]}, headers=headers)
        assert r.status_code == 200
        r = client.get("/v1/digests/settings?owner_type=user&owner_id=u1", headers=headers)
        assert r.json()["memory_range"] == "LAST_1_DAY"

    reg.close_all()
    reset_registry()


def test_digests_publisher(product_pool):
    """Publisher 扫 pending_owners 入队 overall。"""
    ensure_digests_schema(product_pool)
    membox = MemBoxStore(product_pool, dims=1536)
    membox.upsert(["b1"], [_vec()], [{"user_id": "u1", "run_id": "r1", "topic": "t", "content": "c"}])
    pending = MemBoxPendingQueue()
    pending.push("b1", "user", "u1")
    publisher = DigestsPublisher(pending, "p1")
    n = publisher.publish_round()
    assert n == 1
    # 入队后 pending 仍在(aggregate 时 drain);publisher 不 drain
    assert pending.has_pending("user", "u1")


def test_memscene_gc_deletes_stale(product_pool):
    """GC 清理过期 range=ALL memscene。"""
    ensure_digests_schema(product_pool)
    membox = MemBoxStore(product_pool, dims=1536)
    memscene = MemSceneStore(product_pool, dims=1536)
    # 写一条 scene_id="all" 的旧记录(updated_at 早于 cutoff)
    from datetime import datetime, timezone, timedelta

    old_time = (datetime.now(timezone.utc) - timedelta(seconds=3600)).isoformat()
    memscene.upsert(["old1"], [_vec()], [{"owner_id": "u1", "owner_type": "user", "scene_id": "all", "version_id": "1", "content": "old", "range": "ALL", "updated_at": old_time}])
    gc = MemSceneGC(membox, memscene, interval=3600)
    deleted = gc.gc_once(max_age=1800)
    assert deleted >= 1
    assert memscene.get("old1") is None
