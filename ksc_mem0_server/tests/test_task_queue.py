"""异步任务子系统测试:队列 + 多 product 路由消费 + serialize_key FIFO。"""

import threading
import time
import types

from ksc_mem0_server.tasks import (
    TaskConsumer,
    TaskItem,
    GlobalTaskQueue,
    clear_handlers,
    register_handler,
)
from ksc_mem0_server.runtime import get_registry, reset_registry


def test_task_item_auto_id():
    item = TaskItem(category="c", task_type="t", product_id="p", user_id="u")
    assert item.task_id  # 自动 uuid4


def test_queue_put_get():
    q = GlobalTaskQueue(maxsize=10)
    item = TaskItem(category="a", task_type="b", product_id="p1", user_id="u1")
    assert q.put(item) is True
    got = q.get(timeout=1)
    assert got is item
    assert got.product_id == "p1"


def test_queue_full_drop():
    q = GlobalTaskQueue(maxsize=1)
    assert q.put(TaskItem(category="a", task_type="b", product_id="p", user_id="u")) is True
    # 非阻塞(timeout=None),队列满立即丢弃
    assert q.put(TaskItem(category="a", task_type="b", product_id="p", user_id="u")) is False


def test_consumer_routes_by_product(pg_settings, mock_mem0_factories):
    clear_handlers()
    reset_registry()
    q = GlobalTaskQueue(maxsize=10)
    stop = threading.Event()
    done = threading.Event()
    handled: dict = {}

    def handle(item, runtime):
        handled["product_id"] = item.product_id
        handled["user_id"] = item.user_id
        handled["runtime_pid"] = runtime.product_id
        done.set()

    register_handler("profile", "review", types.SimpleNamespace(handle=handle))
    consumer = TaskConsumer(stop, q, get_registry(), concurrency=2)
    consumer.start()
    try:
        q.put(TaskItem(category="profile", task_type="review", product_id="p1", user_id="u1", payload={}))
        assert done.wait(10), "handler not invoked"
        assert handled["product_id"] == "p1"
        assert handled["user_id"] == "u1"
        assert handled["runtime_pid"] == "p1"  # 路由到 p1 的 runtime
    finally:
        stop.set()
        consumer.join(timeout=2)


def test_consumer_no_handler_drops(pg_settings, mock_mem0_factories):
    clear_handlers()
    reset_registry()
    q = GlobalTaskQueue(maxsize=10)
    stop = threading.Event()
    consumer = TaskConsumer(stop, q, get_registry(), concurrency=1)
    consumer.start()
    try:
        q.put(TaskItem(category="unknown", task_type="x", product_id="p1", user_id="u1"))
        time.sleep(1.0)  # 给消费时间
        assert consumer.is_idle() is True
    finally:
        stop.set()
        consumer.join(timeout=2)


def test_consumer_serialize_key_fifo(pg_settings, mock_mem0_factories):
    clear_handlers()
    reset_registry()
    q = GlobalTaskQueue(maxsize=10)
    stop = threading.Event()
    order: list[str] = []
    lock = threading.Lock()

    def handle(item, runtime):
        with lock:
            order.append(item.task_id)
        time.sleep(0.05)

    register_handler("p", "r", types.SimpleNamespace(handle=handle))
    consumer = TaskConsumer(stop, q, get_registry(), concurrency=4)
    consumer.start()
    try:
        items = [TaskItem(category="p", task_type="r", product_id="p1", user_id="u", payload={}, serialize_key="k") for _ in range(3)]
        for it in items:
            q.put(it)
        deadline = time.time() + 10
        while len(order) < 3 and time.time() < deadline:
            time.sleep(0.05)
        assert len(order) == 3
        # 同 serialize_key 严格按入队顺序
        assert order == [it.task_id for it in items]
    finally:
        stop.set()
        consumer.join(timeout=2)
