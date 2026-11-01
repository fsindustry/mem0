"""摘要记忆测试:存储 + 闭环(mock LLM,真 PG)。"""

import json
from unittest.mock import MagicMock

from ksc_mem0_server.summary import (
    SummaryReviewer,
    SummaryRuntime,
    SummaryStrategyStore,
    SummaryTaskStore,
    SummaryVectorStore,
    ensure_summary_schema,
)
from ksc_mem0_server.summary.prompts import parse_summary_result
from ksc_mem0_server.strategy import StrategyStore


def _vec(dim=1536):
    return [0.1] * dim


def test_parse_summary_result():
    raw = json.dumps([{"section": "overview", "content": "讨论了咖啡偏好"}, {"section": "new_facts", "content": "用户喜欢手冲"}])
    items = parse_summary_result(raw)
    assert len(items) == 2
    assert items[0]["section"] == "overview"


def test_parse_summary_result_filters_invalid_section():
    raw = json.dumps([{"section": "bogus", "content": "x"}, {"section": "next_steps", "content": "买咖啡豆"}])
    items = parse_summary_result(raw)
    assert len(items) == 1 and items[0]["section"] == "next_steps"


def test_summary_store_upsert_search(product_pool):
    ensure_summary_schema(product_pool)
    store = SummaryVectorStore(product_pool, dims=1536)
    store.upsert(["s1"], [_vec()], [{"user_id": "u1", "run_id": "r1", "section": "overview", "data": "讨论咖啡", "strategy_id": "default"}])
    results = store.search(_vec(), filters={"user_id": "u1", "run_id": "r1"}, limit=5)
    assert len(results) == 1
    assert results[0]["payload"]["section"] == "overview"


def test_summary_store_requires_scope(product_pool):
    ensure_summary_schema(product_pool)
    store = SummaryVectorStore(product_pool, dims=1536)
    try:
        store.search(_vec(), filters={}, limit=5)
        assert False, "should require scope"
    except ValueError:
        pass


def test_summary_task_store_claim_publish(product_pool):
    ensure_summary_schema(product_pool)
    ts = SummaryTaskStore(product_pool, "p1")
    ts.register("u1", "r1", "op1", {"text": "新消息"}, ready=True)
    assert len(ts.due()) == 1
    run = ts.claim("u1", "r1", strategies=[{"strategy_id": "default", "name": "默认", "prompt": ""}])
    assert run is not None
    assert run["run_id"] == "r1"
    assert run["inputs"] == [{"text": "新消息"}]
    ts.publish(run)
    assert ts.is_published(run["review_id"])
    assert ts.due() == []


def test_summary_runtime_full_loop(product_pool):
    ensure_summary_schema(product_pool)
    strategy = StrategyStore(product_pool, "p1")
    created = strategy.create_strategy("默认", "summary", "摘要偏好")
    strategy_id = created["id"]
    sstore = SummaryVectorStore(product_pool, dims=1536)
    sstrategy = SummaryStrategyStore(product_pool, "p1", strategy)
    tasks = SummaryTaskStore(product_pool, "p1")

    llm = MagicMock()
    llm.generate_response.return_value = json.dumps([{"section": "overview", "content": "讨论了咖啡"}, {"section": "new_facts", "content": "用户喜欢手冲咖啡"}])
    embedder = MagicMock()
    embedder.embed.return_value = _vec()
    reviewer = SummaryReviewer(llm, embedder, sstore, sstrategy)
    runtime = SummaryRuntime(tasks, sstore, sstrategy, reviewer)

    runtime.trigger("u1", "r1", inputs=[{"text": "I love hand-brewed coffee"}])
    runtime.review("u1", "r1")

    items = sstore.list(filters={"user_id": "u1", "run_id": "r1"}, limit=20)
    assert len(items) == 2
    sections = {i["payload"]["section"] for i in items}
    assert sections == {"overview", "new_facts"}
    assert all(i["payload"].get("snapshot_id") for i in items)  # snapshot_id 已写
    assert all(i["payload"]["strategy_id"] == strategy_id for i in items)
    assert tasks.due() == []


def test_summary_runtime_no_strategy_noop(product_pool):
    ensure_summary_schema(product_pool)
    strategy = StrategyStore(product_pool, "p1")  # 无 summary 策略
    sstore = SummaryVectorStore(product_pool, dims=1536)
    sstrategy = SummaryStrategyStore(product_pool, "p1", strategy)
    tasks = SummaryTaskStore(product_pool, "p1")
    reviewer = SummaryReviewer(MagicMock(), MagicMock(), sstore, sstrategy)
    runtime = SummaryRuntime(tasks, sstore, sstrategy, reviewer)

    runtime.trigger("u1", "r1", inputs=[{"text": "x"}])
    runtime.review("u1", "r1")
    # 无策略 → noop publish,无摘要写入
    assert sstore.count(filters={"user_id": "u1", "run_id": "r1"}) == 0
    assert tasks.due() == []
