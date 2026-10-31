"""StrategyStore 测试:CRUD + 三类型分桶 + global merged_prompt + 跨实例版本刷新。真 PG。"""

from ksc_mem0_server.strategy import STRATEGY_TYPES, StrategyStore, get_strategy_types


def test_get_strategy_types():
    types = get_strategy_types()
    assert {t["value"] for t in types} == set(STRATEGY_TYPES)
    for t in types:
        assert "template" in t and t["template"]


def test_strategy_store_create_get(product_pool):
    store = StrategyStore(product_pool, "p1")
    created = store.create_strategy("my-fact", "fact", "提取饮食偏好")
    s = store.get_strategy(created["id"])
    assert s["name"] == "my-fact"
    assert s["strategy_type"] == "fact"
    assert s["status"] == 1
    assert s["prompt"] == "提取饮食偏好"


def test_strategy_store_list_and_filter(product_pool):
    store = StrategyStore(product_pool, "p1")
    store.create_strategy("f1", "fact", "p")
    store.create_strategy("pr1", "profile", "p")
    assert len(store.list_strategies()) == 2
    facts = store.list_strategies(strategy_type="fact")
    assert len(facts) == 1 and facts[0]["strategy_type"] == "fact"


def test_strategy_store_snapshot_by_type(product_pool):
    store = StrategyStore(product_pool, "p1")
    store.create_strategy("f1", "fact", "fp")
    store.create_strategy("pr1", "profile", "pp")
    store.create_strategy("g1", "global", "gp")
    assert len(store.get_enabled_strategies_by_type("fact")) == 1
    assert store.get_enabled_strategies_by_type("fact")[0]["prompt"] == "fp"
    assert len(store.get_enabled_strategies_by_type("profile")) == 1
    # global 不进类型桶
    assert store.get_enabled_strategies_by_type("global") == []


def test_strategy_store_merged_prompt(product_pool):
    store = StrategyStore(product_pool, "p1")
    store.create_strategy("g1", "global", "global-prompt-1")
    store.create_strategy("g2", "global", "global-prompt-2")
    mp = store.get_merged_prompt()
    assert "global-prompt-1" in mp
    assert "global-prompt-2" in mp
    assert "---" in mp


def test_strategy_store_merged_prompt_empty_without_global(product_pool):
    store = StrategyStore(product_pool, "p1")
    store.create_strategy("f1", "fact", "fp")
    assert store.get_merged_prompt() == ""


def test_strategy_store_check_and_reload_detects_cross_instance(product_pool):
    store = StrategyStore(product_pool, "p1")
    rev0 = store.current_revision()
    # 另一实例(模拟跨 worker)写入
    store2 = StrategyStore(product_pool, "p1")
    store2.create_strategy("f1", "fact", "p")
    changed = store.check_and_reload()
    assert changed is True
    assert store.current_revision() > rev0
    assert len(store.get_enabled_strategies_by_type("fact")) == 1


def test_strategy_store_check_and_reload_no_change(product_pool):
    store = StrategyStore(product_pool, "p1")
    store.create_strategy("f1", "fact", "p")
    assert store.check_and_reload() is False


def test_strategy_store_update(product_pool):
    store = StrategyStore(product_pool, "p1")
    created = store.create_strategy("f1", "fact", "p")
    store.update_strategy(created["id"], name="new", prompt="p2")
    s = store.get_strategy(created["id"])
    assert s["name"] == "new" and s["prompt"] == "p2"


def test_strategy_store_delete_soft(product_pool):
    store = StrategyStore(product_pool, "p1")
    created = store.create_strategy("f1", "fact", "p")
    store.delete_strategy(created["id"])
    assert store.get_strategy(created["id"])["status"] == -1
    assert store.list_strategies() == []
    assert len(store.list_strategies(include_deleted=True)) == 1


def test_strategy_store_disable_enable(product_pool):
    store = StrategyStore(product_pool, "p1")
    created = store.create_strategy("f1", "fact", "p")
    store.set_strategy_status(created["id"], 0)
    assert store.get_enabled_strategies_by_type("fact") == []
    store.set_strategy_status(created["id"], 1)
    assert len(store.get_enabled_strategies_by_type("fact")) == 1
