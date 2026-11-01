"""SummaryRuntime:摘要 review 协调闭环(claim → 多策略 fan-out LLM → embed+upsert → publish)。

迁移自参考库 summary/runtime.py。与 ProfileRuntime 对称,但:
  - K=(user_id, run_id) session 级
  - 一次 claim 内 fan-out 多策略(strategies_snapshot 冻结)
  - 每个 strategy 生成 entries(7 章节),全部 upsert 到 mem0_summary,payload 带 strategy_id/section
  - active_snapshot_id = review_id(无数值 version)
"""

import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List


logger = logging.getLogger(__name__)


class LeaseLost(Exception):
    pass


class LeaseHeartbeat:
    def __init__(self, tasks, run, lease_seconds: int = 120, timeout: int = 900):
        self.tasks = tasks
        self.run = run
        self.lease_seconds = lease_seconds
        self.stop = threading.Event()
        self.lost = threading.Event()
        self.deadline = time.monotonic() + timeout
        self.thread = threading.Thread(target=self._loop, name="summary-lease-heartbeat", daemon=True)

    def _loop(self):
        interval = max(5, self.lease_seconds // 4)
        while not self.stop.wait(interval):
            try:
                self.check()
                self.tasks.heartbeat(self.run)
            except Exception:
                self.lost.set()
                return

    def check(self):
        if self.lost.is_set() or time.monotonic() >= self.deadline:
            raise LeaseLost("Summary heartbeat failed")

    def __enter__(self):
        self.tasks.heartbeat(self.run)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=1)


class SummaryRuntime:
    def __init__(self, task_store, summary_store, strategy_store, reviewer) -> None:
        self.tasks = task_store
        self.store = summary_store
        self.strategy_store = strategy_store
        self.reviewer = reviewer

    def review(self, user_id: str, run_id: str) -> None:
        strategies = self.strategy_store.list_active_summary_strategies()
        run = self.tasks.claim(user_id, run_id, strategies=strategies)
        if run is None:
            return
        logger.info(
            "summary_claim user=%s run=%s review=%s strategies=%d",
            user_id, run_id, run["review_id"], len(strategies),
        )
        try:
            with LeaseHeartbeat(self.tasks, run, self.tasks.lease_seconds) as heartbeat:
                self._publish(run, self._generate(run, strategies, heartbeat), heartbeat)
        except Exception as error:
            if self.tasks.is_published(run["review_id"]):
                return
            self.tasks.fail(run, str(error))
            raise

    def _generate(self, run, strategies, heartbeat) -> List[Dict[str, Any]]:
        if not strategies or not run["inputs"]:
            return []
        all_entries: List[Dict[str, Any]] = []
        for strategy in strategies:
            heartbeat.check()
            entries = self.reviewer.generate_strategy(strategy, run["user_id"], run["run_id"], run["inputs"])
            for e in entries:
                e["strategy_id"] = strategy.get("strategy_id", "default")
                e["strategy_name"] = strategy.get("name", strategy.get("strategy_id", "default"))
                all_entries.append(e)
        return all_entries

    def _publish(self, run, entries, heartbeat) -> None:
        if not entries:
            heartbeat.check()
            self.tasks.materialize(run, [])
            self.tasks.publish(run, noop=True)
            return
        now = datetime.now(timezone.utc).isoformat()
        manifest = []
        for e in entries:
            heartbeat.check()
            payload = {
                "user_id": run["user_id"],
                "run_id": run["run_id"],
                "strategy_id": e.get("strategy_id", "default"),
                "strategy_name": e.get("strategy_name", "default"),
                "section": e.get("section", ""),
                "data": e.get("content", ""),
                "snapshot_id": run["review_id"],
                "created_at": now,
                "updated_at": now,
            }
            pid = str(uuid.uuid4())
            vector = self.reviewer.embedding_model.embed(payload["data"], "add")
            manifest.append({"id": pid, "metadata": payload, "vectors": vector})
        heartbeat.check()
        self.tasks.materialize(run, manifest)
        if manifest:
            self.store.upsert(
                [m["id"] for m in manifest], [m["vectors"] for m in manifest], [m["metadata"] for m in manifest]
            )
        heartbeat.check()
        self.tasks.publish(run)

    def trigger(self, user_id: str, run_id: str, inputs: list, operation: str = None) -> str:
        op = operation or uuid.uuid4().hex
        return self.tasks.register(user_id, run_id, op, inputs, ready=True, event_type="MANUAL_REVIEW")
