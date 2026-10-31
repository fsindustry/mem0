"""API 集成测试:health / products 登记 / 事实记忆端点(mock runtime)/ 鉴权 401。"""

import uuid
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from ksc_mem0_server.api.memories import get_runtime as memories_get_runtime
from ksc_mem0_server.app import create_app
from ksc_mem0_server.config import reset_settings


def _client():
    return TestClient(create_app())


def test_health(pg_settings):
    with _client() as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


def test_products_register_and_list(pg_settings):
    pid = f"api-{uuid.uuid4().hex[:8]}"
    with _client() as client:
        r = client.post("/v1/products", json={"product_id": pid})
        assert r.status_code == 200
        assert r.json()["product_id"] == pid
        r = client.get("/v1/products")
        assert any(p["product_id"] == pid for p in r.json()["results"])

    # 重复登记 409
    with _client() as client:
        r = client.post("/v1/products", json={"product_id": pid})
        assert r.status_code == 409


def _mock_runtime():
    mock_facts = MagicMock()
    mock_facts.add.return_value = {"results": [{"id": "x", "memory": "m", "event": "ADD"}]}
    mock_facts.search.return_value = {"results": [{"id": "x", "memory": "m"}]}
    mock_facts.get.return_value = {"id": "x", "memory": "m"}
    mock_facts.get_all.return_value = {"results": []}
    mock_facts.update.return_value = {"message": "ok"}
    mock_facts.delete.return_value = {"message": "ok"}
    mock_facts.delete_all.return_value = {"message": "ok"}
    mock_facts.history.return_value = [{"id": "h1", "event": "ADD"}]
    rt = MagicMock()
    rt.facts = mock_facts
    return rt, mock_facts


def test_memories_endpoints_route_to_runtime(pg_settings):
    app = create_app()
    rt, mock_facts = _mock_runtime()
    app.dependency_overrides[memories_get_runtime] = lambda: rt
    headers = {"X-Product-Id": "p1"}
    with TestClient(app) as client:
        r = client.post("/memories", json={"messages": [{"role": "user", "content": "hi"}], "user_id": "u1"}, headers=headers)
        assert r.status_code == 200
        mock_facts.add.assert_called_once()

        r = client.post("/search", json={"query": "q", "user_id": "u1"}, headers=headers)
        assert r.status_code == 200
        mock_facts.search.assert_called_once()
        assert mock_facts.search.call_args.kwargs["filters"]["user_id"] == "u1"

        r = client.get("/memories?user_id=u1", headers=headers)
        assert r.status_code == 200
        mock_facts.get_all.assert_called_once()
        assert mock_facts.get_all.call_args.kwargs["filters"]["user_id"] == "u1"

        r = client.get("/memories/x", headers=headers)
        assert r.status_code == 200
        mock_facts.get.assert_called_once_with("x")

        r = client.put("/memories/x", json={"data": "new"}, headers=headers)
        assert r.status_code == 200
        mock_facts.update.assert_called_once()

        r = client.delete("/memories/x", headers=headers)
        assert r.status_code == 200
        mock_facts.delete.assert_called_once_with("x")

        r = client.delete("/memories?user_id=u1", headers=headers)
        assert r.status_code == 200
        mock_facts.delete_all.assert_called_once()

        r = client.get("/memories/x/history", headers=headers)
        assert r.status_code == 200
        mock_facts.history.assert_called_once_with("x")


def test_missing_api_key_returns_401(pg_settings, monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("ADMIN_API_KEY", "admin-secret")
    reset_settings()
    with _client() as client:
        r = client.post("/memories", json={"messages": [{"role": "user", "content": "hi"}]}, headers={"X-Product-Id": "p1"})
        assert r.status_code == 401
        r = client.post(
            "/memories",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers={"X-Product-Id": "p1", "X-API-Key": "wrong"},
        )
        assert r.status_code == 401
