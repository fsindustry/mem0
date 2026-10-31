"""策略 API 路由测试:类型字典 + CRUD(mock runtime)。"""

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from ksc_mem0_server.api.strategies import get_runtime as strategies_get_runtime
from ksc_mem0_server.app import create_app


def test_strategy_types_endpoint(pg_settings):
    with TestClient(create_app()) as client:
        r = client.get("/v1/strategies/types")
        assert r.status_code == 200
        assert {t["value"] for t in r.json()["results"]} == {"profile", "fact", "summary"}


def _mock_runtime_with_store():
    store = MagicMock()
    store.create_strategy.return_value = {"id": "s1", "name": "n", "strategy_type": "fact", "prompt": "p", "status": 1}
    store.get_strategy.return_value = {"id": "s1", "name": "n", "strategy_type": "fact", "prompt": "p", "status": 1}
    store.list_strategies.return_value = [{"id": "s1", "name": "n", "strategy_type": "fact"}]
    rt = MagicMock()
    rt.strategy_store = store
    return rt, store


def test_strategies_crud_routes(pg_settings):
    app = create_app()
    rt, store = _mock_runtime_with_store()
    app.dependency_overrides[strategies_get_runtime] = lambda: rt
    headers = {"X-Product-Id": "p1"}
    with TestClient(app) as client:
        r = client.post("/v1/strategies", json={"name": "n", "strategy_type": "fact", "prompt": "p"}, headers=headers)
        assert r.status_code == 200
        assert r.json()["id"] == "s1"
        store.create_strategy.assert_called_once_with("n", "fact", "p")

        r = client.get("/v1/strategies", headers=headers)
        assert r.status_code == 200
        assert len(r.json()["results"]) == 1

        r = client.get("/v1/strategies/s1", headers=headers)
        assert r.status_code == 200

        r = client.put("/v1/strategies/s1", json={"prompt": "p2"}, headers=headers)
        assert r.status_code == 200
        store.update_strategy.assert_called_once()

        r = client.delete("/v1/strategies/s1", headers=headers)
        assert r.status_code == 200
        store.delete_strategy.assert_called_once_with("s1")


def test_strategies_invalid_type_400(pg_settings):
    app = create_app()
    rt, _ = _mock_runtime_with_store()
    app.dependency_overrides[strategies_get_runtime] = lambda: rt
    with TestClient(app) as client:
        r = client.post(
            "/v1/strategies",
            json={"name": "n", "strategy_type": "bogus", "prompt": "p"},
            headers={"X-Product-Id": "p1"},
        )
        assert r.status_code == 400


def test_strategies_not_found_404(pg_settings):
    app = create_app()
    rt, store = _mock_runtime_with_store()
    store.get_strategy.return_value = None
    app.dependency_overrides[strategies_get_runtime] = lambda: rt
    with TestClient(app) as client:
        r = client.get("/v1/strategies/none", headers={"X-Product-Id": "p1"})
        assert r.status_code == 404
