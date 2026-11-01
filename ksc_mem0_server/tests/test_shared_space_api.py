"""P4d shared_space API + ProductRuntime 接入测试。"""

from fastapi.testclient import TestClient

from ksc_mem0_server.app import create_app
from ksc_mem0_server.runtime import get_registry, reset_registry


def test_shared_space_api_crud(pg_settings, mock_mem0_factories):
    reset_registry()
    reg = get_registry()
    reg.get_or_create("p1")
    app = create_app()
    headers = {"X-Product-Id": "p1"}
    with TestClient(app) as client:
        # 创建空间
        r = client.post("/v1/shared_spaces", json={"name": "team-coffee", "extract_enabled": True, "token_threshold": 100}, headers=headers)
        assert r.status_code == 200
        space_id = r.json()["space_id"]
        assert r.json()["extract_enabled"] is True

        # 重名 409
        r = client.post("/v1/shared_spaces", json={"name": "team-coffee"}, headers=headers)
        assert r.status_code == 409

        # 列表
        r = client.get("/v1/shared_spaces", headers=headers)
        assert r.json()["total"] == 1

        # get
        r = client.get(f"/v1/shared_spaces/{space_id}", headers=headers)
        assert r.json()["name"] == "team-coffee"

        # rename
        r = client.put(f"/v1/shared_spaces/{space_id}/name", json={"name": "coffee-team"}, headers=headers)
        assert r.json()["name"] == "coffee-team"

        # 成员
        r = client.post(f"/v1/shared_spaces/{space_id}/members", json={"members": [["u1", "user"], ["a1", "agent"]]}, headers=headers)
        assert r.status_code == 200
        assert len(r.json()["added"]) == 2

        r = client.get(f"/v1/shared_spaces/{space_id}/members", headers=headers)
        assert r.json()["total"] == 2

        # 成员反查
        r = client.get("/v1/shared_spaces/members/spaces?member_id=u1&member_type=user", headers=headers)
        assert len(r.json()["results"]) == 1

        # 规则
        r = client.post(f"/v1/shared_spaces/{space_id}/rules", json={"content": "只提取咖啡相关"}, headers=headers)
        assert r.status_code == 200
        rule_id = r.json()["rule_id"]
        r = client.get(f"/v1/shared_spaces/{space_id}/rules", headers=headers)
        assert r.json()["total"] == 1
        r = client.put(f"/v1/shared_spaces/{space_id}/rules/{rule_id}", json={"content": "新规则"}, headers=headers)
        assert r.json()["content"] == "新规则"
        r = client.delete(f"/v1/shared_spaces/{space_id}/rules/{rule_id}", headers=headers)
        assert r.status_code == 200

        # delete 空间
        r = client.delete(f"/v1/shared_spaces/{space_id}", headers=headers)
        assert r.status_code == 200
        r = client.get(f"/v1/shared_spaces/{space_id}", headers=headers)
        assert r.status_code == 404

    reg.close_all()
    reset_registry()


def test_shared_space_api_404_and_400(pg_settings, mock_mem0_factories):
    reset_registry()
    reg = get_registry()
    reg.get_or_create("p1")
    app = create_app()
    headers = {"X-Product-Id": "p1"}
    with TestClient(app) as client:
        r = client.get("/v1/shared_spaces/no-such", headers=headers)
        assert r.status_code == 404
        # 创建空间后,非法 member_type 触发 400(空间存在,member_type 校验在前)
        r = client.post("/v1/shared_spaces", json={"name": "s1"}, headers=headers)
        space_id = r.json()["space_id"]
        r = client.post(f"/v1/shared_spaces/{space_id}/members", json={"members": [["u1", "bogus"]]}, headers=headers)
        assert r.status_code == 400
        # 成员反查非法 member_type 400
        r = client.get("/v1/shared_spaces/members/spaces?member_id=u1&member_type=bogus", headers=headers)
        assert r.status_code == 400
    reg.close_all()
    reset_registry()
