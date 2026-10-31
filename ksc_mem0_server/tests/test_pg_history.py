"""PostgreSQLHistoryManager 测试:对齐 SQLiteManager 7 方法,真 PG。

蓝本:tests/vector_stores/test_pgvector.py 的 PG 连接 + 事务模式。
"""

from ksc_mem0_server.storage.pg_history import PostgreSQLHistoryManager


def test_add_and_get_history(history_pool):
    mgr = PostgreSQLHistoryManager(history_pool)
    mgr.add_history("mem-1", None, "用户喜欢咖啡", "ADD", actor_id="u1", role="user")
    hist = mgr.get_history("mem-1")
    assert len(hist) == 1
    row = hist[0]
    assert row["memory_id"] == "mem-1"
    assert row["new_memory"] == "用户喜欢咖啡"
    assert row["event"] == "ADD"
    assert row["is_deleted"] is False
    assert row["actor_id"] == "u1"
    assert row["role"] == "user"


def test_batch_add_history(history_pool):
    mgr = PostgreSQLHistoryManager(history_pool)
    mgr.batch_add_history(
        [
            {"memory_id": "m1", "new_memory": "a", "event": "ADD"},
            {"memory_id": "m2", "new_memory": "b", "event": "ADD"},
        ]
    )
    assert len(mgr.get_history("m1")) == 1
    assert len(mgr.get_history("m2")) == 1


def test_get_history_empty(history_pool):
    mgr = PostgreSQLHistoryManager(history_pool)
    assert mgr.get_history("nope") == []


def test_history_ordering(history_pool):
    mgr = PostgreSQLHistoryManager(history_pool)
    mgr.add_history("m1", None, "old", "ADD", created_at="2026-01-01T00:00:00+00:00")
    mgr.add_history("m1", "old", "new", "UPDATE", created_at="2026-01-02T00:00:00+00:00")
    hist = mgr.get_history("m1")
    assert len(hist) == 2
    assert hist[0]["new_memory"] == "old"
    assert hist[1]["new_memory"] == "new"


def test_save_and_get_last_messages(history_pool):
    mgr = PostgreSQLHistoryManager(history_pool)
    mgr.save_messages(
        [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        "scope-1",
    )
    last = mgr.get_last_messages("scope-1", limit=10)
    assert len(last) == 2
    assert last[0]["role"] == "user"
    assert last[1]["role"] == "assistant"


def test_messages_eviction_keeps_recent_10(history_pool):
    mgr = PostgreSQLHistoryManager(history_pool)
    for i in range(15):
        mgr.save_messages([{"role": "user", "content": str(i)}], "scope-evict")
    last = mgr.get_last_messages("scope-evict", limit=10)
    assert len(last) == 10
    assert [m["content"] for m in last] == [str(i) for i in range(5, 15)]


def test_reset_drops_and_recreates(history_pool):
    mgr = PostgreSQLHistoryManager(history_pool)
    mgr.add_history("m1", None, "x", "ADD")
    mgr.save_messages([{"role": "user", "content": "y"}], "s1")
    mgr.reset()
    assert mgr.get_history("m1") == []
    assert mgr.get_last_messages("s1") == []
    # reset 后表仍在,可继续写入
    mgr.add_history("m2", None, "z", "ADD")
    assert len(mgr.get_history("m2")) == 1


def test_close_is_noop_on_shared_pool(history_pool):
    mgr = PostgreSQLHistoryManager(history_pool)
    mgr.close()
    # pool 不应被关闭,仍可建新 manager
    mgr2 = PostgreSQLHistoryManager(history_pool)
    mgr2.add_history("m3", None, "after-close", "ADD")
    assert len(mgr2.get_history("m3")) == 1
