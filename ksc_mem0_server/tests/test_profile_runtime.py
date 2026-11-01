"""画像生成闭环测试:review 纯函数 + ProfileRuntime 闭环(mock LLM,真 PG)。

mock LLM 返回 EXTRACT/COMPARE 的 JSON;embedder mock 向量;真 pgvector + 真 task_store。
验证:触发 → claim → 两步 LLM → embed+upsert → publish,画像落 pgvector + state 版本+1。
"""

import json
from unittest.mock import MagicMock

from ksc_mem0_server.profile import (
    ProfileReviewer,
    ProfileRuntime,
    ProfileStore,
    ProfileStrategyStore,
    ProfileTaskStore,
    ensure_profile_schema,
)
from ksc_mem0_server.profile.review import (
    apply_profile_updates,
    finalize_entries,
    parse_compare_result,
    parse_extract_result,
    to_working_profile,
    working_to_entries,
)
from ksc_mem0_server.strategy import StrategyStore


def _vec(dim=1536):
    return [0.1] * dim


# --- 纯函数 ---
def test_parse_extract_result_array():
    raw = json.dumps([{"data": "is a backend engineer", "category": "identity"}, {"data": "prefers concise replies", "category": "agent_habit"}])
    p = parse_extract_result(raw)
    assert len(p) == 2
    assert p[0]["category"] == "identity"


def test_parse_extract_result_markdown_wrapped():
    raw = '```json\n[{"data":"loves coffee","category":"preference"}]\n```'
    p = parse_extract_result(raw)
    assert len(p) == 1 and p[0]["data"] == "loves coffee"


def test_parse_extract_result_invalid_category_filtered():
    raw = json.dumps([{"data": "x", "category": "bogus"}, {"data": "y", "category": "identity"}])
    p = parse_extract_result(raw)
    assert len(p) == 1 and p[0]["category"] == "identity"


def test_parse_compare_result():
    raw = json.dumps({"updated": [{"id": "tmp_a", "mention_count": 1}], "added": [{"ref": "p0"}]})
    r = parse_compare_result(raw)
    assert len(r["updated"]) == 1
    assert r["added"] == [{"ref": "p0"}]


def test_parse_compare_result_empty():
    r = parse_compare_result("not json at all")
    assert r == {"updated": [], "added": []}


def test_apply_profile_updates_strengthen():
    working = [{"id": "tmp_a", "data": "loves coffee", "category": "preference", "mention_count": 1, "updated_at": ""}]
    from datetime import datetime, timezone

    apply_profile_updates(working, [{"id": "tmp_a", "mention_count": 1}], [], datetime.now(timezone.utc))
    assert working[0]["mention_count"] == 2


def test_apply_profile_updates_invalidate():
    working = [{"id": "tmp_a", "data": "loves coffee", "category": "preference", "mention_count": 1, "updated_at": ""}]
    from datetime import datetime, timezone

    apply_profile_updates(working, [{"id": "tmp_a", "mention_count": -1}], [], datetime.now(timezone.utc))
    assert working[0]["mention_count"] == -1
    entries = working_to_entries(working)
    assert entries == []  # 作废条目不落盘


def test_apply_profile_updates_added():
    working = []
    from datetime import datetime, timezone

    apply_profile_updates(working, [], [{"data": "uses Go", "category": "identity"}], datetime.now(timezone.utc))
    assert len(working) == 1
    assert working[0]["data"] == "uses Go"
    assert working[0]["id"].startswith("tmp_")


def test_finalize_entries_heat_and_trim():
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    entries = [{"data": f"d{i}", "category": "identity", "mention_count": 1, "updated_at": now} for i in range(50)]
    out = finalize_entries(entries, now, {"identity": 5, "agent_habit": 0, "preference": 0}, 100)
    # 总量未超 100,单类不裁(与参考库语义一致:总量未满不裁单类)
    assert len(out) == 50
    assert all("heat_score" in e for e in out)


def test_to_working_profile_skips_invalidated():
    old = [
        {"id": "a", "payload": {"data": "x", "category": "identity", "mention_count": -1, "updated_at": ""}},
        {"id": "b", "payload": {"data": "y", "category": "preference", "mention_count": 1, "updated_at": ""}},
    ]
    working = to_working_profile(old)
    assert len(working) == 1 and working[0]["id"] == "b"


# --- 闭环 ---
def _mock_llm(extract_items, compare_obj):
    """返回一个 mock LLM,按调用顺序返回 extract JSON 再返回 compare JSON。"""
    llm = MagicMock()
    responses = [json.dumps(extract_items), json.dumps(compare_obj)]
    call = {"i": 0}

    def gen(**kwargs):
        i = call["i"]
        call["i"] += 1
        return responses[min(i, len(responses) - 1)]

    llm.generate_response.side_effect = gen
    return llm


def test_profile_runtime_full_loop(product_pool):
    ensure_profile_schema(product_pool)
    strategy = StrategyStore(product_pool, "p1")
    strategy.create_strategy("default", "profile", "提取画像")
    pstore = ProfileStore(product_pool, dims=1536)
    pstrategy = ProfileStrategyStore(product_pool, "p1", strategy)
    tasks = ProfileTaskStore(product_pool, "p1")

    # mock LLM:EXTRACT 返回一条 preference,COMPARE 返回 added ref p0
    llm = _mock_llm(
        [{"data": "loves hand-brewed coffee", "category": "preference"}],
        {"updated": [], "added": [{"ref": "p0"}]},
    )
    embedder = MagicMock()
    embedder.embed.return_value = _vec()
    reviewer = ProfileReviewer(llm, embedder, pstore, pstrategy)
    runtime = ProfileRuntime(tasks, pstore, strategy, reviewer)

    # 触发:登记就绪事件(新记忆作为 inputs)
    runtime.trigger("u1", "default", inputs=[{"text": "I love hand-brewed coffee"}])
    # review 闭环
    runtime.review("u1", "default")

    # 画像落 pgvector
    items = pstore.list(filters={"user_id": "u1", "strategy_id": "default"}, limit=10)
    assert len(items) == 1
    assert "coffee" in items[0]["payload"]["data"]
    assert items[0]["payload"]["category"] == "preference"
    assert items[0]["payload"]["version"] == 2  # state.version 默认 1,base_version+1=2
    # state 版本+1(publish 后)
    assert pstrategy.get_profile_version("u1", "default") == 2
    # 事件已 ACKED
    assert tasks.due() == []


def test_profile_runtime_strengthen_existing(product_pool):
    ensure_profile_schema(product_pool)
    strategy = StrategyStore(product_pool, "p1")
    pstore = ProfileStore(product_pool, dims=1536)
    pstrategy = ProfileStrategyStore(product_pool, "p1", strategy)
    tasks = ProfileTaskStore(product_pool, "p1")

    # 先写入一条已有画像(version 1)
    pstore.upsert(["old1"], [_vec()], [{"user_id": "u1", "strategy_id": "default", "data": "loves coffee", "category": "preference", "mention_count": 1, "version": 1}])
    pstrategy.record_profile_update("u1", "default")

    # LLM:EXTRACT 返回同义 preference,COMPARE 返回强化 old1
    llm = _mock_llm(
        [{"data": "loves hand-brewed coffee", "category": "preference"}],
        {"updated": [{"id": "old1", "mention_count": 1}], "added": []},
    )
    embedder = MagicMock()
    embedder.embed.return_value = _vec()
    reviewer = ProfileReviewer(llm, embedder, pstore, pstrategy)
    runtime = ProfileRuntime(tasks, pstore, strategy, reviewer)

    runtime.trigger("u1", "default", inputs=[{"text": "I love hand-brewed coffee"}])
    runtime.review("u1", "default")

    # 版本升到 2
    assert pstrategy.get_profile_version("u1", "default") == 2
    items = pstore.list(filters={"user_id": "u1", "strategy_id": "default"}, limit=10)
    # 快照语义:旧 v1 保留 + 新 v2 写入(GC 清理旧版本),共 2 条
    v2 = [i for i in items if i["payload"].get("version") == 2]
    assert len(v2) == 1
    assert v2[0]["payload"]["data"] == "loves coffee"
    assert v2[0]["payload"]["mention_count"] == 2  # 旧 1 + 强化 +1
