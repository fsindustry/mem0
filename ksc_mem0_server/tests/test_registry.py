"""ProductRegistry 测试:按需建库 + 缓存 + 并发安全。mock mem0 工厂(避免真 OpenAI)。"""

import threading
import uuid

import psycopg

from ksc_mem0_server.db import SessionLocal
from ksc_mem0_server.models import Product
from ksc_mem0_server.runtime import get_registry, reset_registry


def test_registry_creates_and_caches(pg_settings, mock_mem0_factories):
    reset_registry()
    reg = get_registry()
    pid = f"reg-{uuid.uuid4().hex[:8]}"

    rt1 = reg.get_or_create(pid)
    rt2 = reg.get_or_create(pid)
    assert rt1 is rt2  # 缓存命中

    # 应用库已登记
    with SessionLocal() as session:
        assert session.get(Product, pid) is not None

    # product 库已建
    db_name = pg_settings.product_db_name(pid)
    with psycopg.connect(pg_settings.pg_conninfo(db_name)) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        assert cur.fetchone()[0] == 1

    reg.close_all()


def test_registry_concurrent_same_pid_single_runtime(pg_settings, mock_mem0_factories):
    reset_registry()
    reg = get_registry()
    pid = f"conc-{uuid.uuid4().hex[:8]}"

    results: list = []
    barrier = threading.Barrier(4)

    def go():
        barrier.wait()
        results.append(reg.get_or_create(pid))

    threads = [threading.Thread(target=go) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 4
    assert all(r is results[0] for r in results)  # 同一 runtime 实例
    reg.close_all()


def test_registry_close_all_releases(pg_settings, mock_mem0_factories):
    reset_registry()
    reg = get_registry()
    pid = f"close-{uuid.uuid4().hex[:8]}"
    rt = reg.get_or_create(pid)
    reg.close_all()
    assert reg.get(pid) is None
    # 关闭后 pool 不可用;新 get_or_create 重建
    rt2 = reg.get_or_create(pid)
    assert rt2 is not rt
    reg.close_all()
