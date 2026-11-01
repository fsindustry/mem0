"""失败补偿测试:ExtractionFailureStore 状态机 + scanner 补偿。真 PG。"""

import threading
import time

from ksc_mem0_server.extraction import ExtractionFailureStore, FailureCompensationScanner
from ksc_mem0_server.runtime import get_registry, reset_registry


def test_failure_record_and_due(product_pool):
    store = ExtractionFailureStore(product_pool, "p1")
    fid = store.record("ep1", "s1", "extract", error_type="LLM_ERROR", error_msg="timeout", user_id="u1")
    due = store.due()
    assert len(due) == 1
    assert due[0]["id"] == fid
    assert due[0]["episode_id"] == "ep1"
    assert due[0]["strategy_id"] == "s1"


def test_failure_mark_running_cas(product_pool):
    store = ExtractionFailureStore(product_pool, "p1")
    fid = store.record("ep1", "s1", "extract")
    owner = store.mark_running(fid)
    assert owner is not None
    # 二次抢占失败(已 RUNNING)
    assert store.mark_running(fid) is None


def test_failure_mark_succeeded(product_pool):
    store = ExtractionFailureStore(product_pool, "p1")
    fid = store.record("ep1", "s1", "extract")
    owner = store.mark_running(fid)
    assert store.mark_succeeded(fid, owner) is True
    # SUCCEEDED 不再 due
    assert store.due() == []


def test_failure_bump_retry_then_dead(product_pool):
    from ksc_mem0_server.extraction import failures as fmod

    store = ExtractionFailureStore(product_pool, "p1")
    fid = store.record("ep1", "s1", "extract")
    owner = store.mark_running(fid)
    # 连续失败直到超过 MAX_RETRY
    for _ in range(fmod.MAX_RETRY):
        store.bump_retry(fid, owner)
        owner = store.mark_running(fid)  # PENDING→RUNNING 再失败
        if owner is None:
            break
    # 最终变 DEAD
    statuses = [r["status"] for r in store.list()]
    assert "DEAD" in statuses


def test_failure_reset_to_pending(product_pool):
    store = ExtractionFailureStore(product_pool, "p1")
    fid = store.record("ep1", "s1", "extract")
    # 强制设 DEAD
    owner = store.mark_running(fid)
    for _ in range(10):
        store.bump_retry(fid, owner)
        owner = store.mark_running(fid)
        if owner is None:
            break
    # reset DEAD → PENDING
    assert store.reset_to_pending(fid) is True
    assert len(store.due()) == 1


def test_failure_record_upsert_existing(product_pool):
    store = ExtractionFailureStore(product_pool, "p1")
    store.record("ep1", "s1", "extract", error_msg="err1")
    store.record("ep1", "s1", "extract", error_msg="err2")  # 同 episode+strategy
    # upsert 不新增行(同唯一键),仍是 1 条
    assert len(store.list()) == 1


def test_scanner_compensates(pg_settings, mock_mem0_factories):
    reset_registry()
    reg = get_registry()
    rt = reg.get_or_create("p1")
    store = rt.extraction_failure_store
    store.record("ep1", "s1", "extract", strategy_type="fact", user_id="u1")

    calls: list = []

    def rerun(runtime, failure):
        calls.append(failure["id"])
        return True

    stop = threading.Event()
    scanner = FailureCompensationScanner(stop, reg, rerun, interval=0.5)
    scanner.start()
    try:
        deadline = time.time() + 10
        while not calls and time.time() < deadline:
            time.sleep(0.1)
        assert calls, "scanner did not compensate"
        # 补偿成功 → SUCCEEDED,不再 due
        assert store.due() == []
    finally:
        stop.set()
        scanner.join(timeout=2)
    reg.close_all()
