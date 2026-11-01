"""ProfileRuntime:画像 review 协调闭环(claim → 两步 LLM → embed+upsert → publish)。

迁移自参考库 profile/runtime.py,简化:
  - 去掉 source_journal/croniter/metrics/verify_snapshot 外围
  - 新记忆由触发方经 task_store.register 注入 run["inputs"](非 source journal)
  - 旧画像从 profile_store.list 取(非 snapshot_items)
保留核心:租约心跳、两步 LLM、滚动 apply、热度裁剪、is_published 防误判、失败补偿。
"""

import logging
import threading
import time
import uuid
from datetime import datetime, timezone

from .review import (
    ProfileReviewConfig,
    apply_profile_updates,
    call_with_parse_retry,
    compact_working_profile,
    finalize_entries,
    parse_compare_result,
    parse_extract_result,
    to_working_profile,
    working_to_entries,
    _resolve_added_refs,
)

logger = logging.getLogger(__name__)


class LeaseLost(Exception):
    pass


class LeaseHeartbeat:
    """租约心跳:后台线程定期续租约,超时/丢失抛 LeaseLost。"""

    def __init__(self, tasks, run, lease_seconds: int = 120, timeout: int = 900):
        self.tasks = tasks
        self.run = run
        self.lease_seconds = lease_seconds
        self.stop = threading.Event()
        self.lost = threading.Event()
        self.deadline = time.monotonic() + timeout
        self.thread = threading.Thread(target=self._loop, name="profile-lease-heartbeat", daemon=True)

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
            raise LeaseLost("Profile heartbeat failed")

    def __enter__(self):
        self.tasks.heartbeat(self.run)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=1)


class ProfileRuntime:
    """单 product 的画像生成协调器。"""

    def __init__(self, task_store, profile_store, strategy_store, reviewer) -> None:
        self.tasks = task_store
        self.store = profile_store
        self.strategy_store = strategy_store
        self.reviewer = reviewer

    def review(self, user_id: str, strategy_id: str) -> None:
        run = self.tasks.claim(user_id, strategy_id)
        if run is None:
            return
        logger.info(
            "profile_claim user=%s strategy=%s review=%s target=%s",
            user_id, strategy_id, run["review_id"], run["target_generation"],
        )
        try:
            with LeaseHeartbeat(self.tasks, run, self.tasks.lease_seconds) as heartbeat:
                self._publish(run, self._generate(run, heartbeat), heartbeat)
        except Exception as error:
            # 连接可能在 COMMIT 后断开;先查持久结果,已发布则不重复 fail
            if self.tasks.is_published(run["review_id"]):
                return
            self.tasks.fail(run, str(error))
            raise

    def _generate(self, run, heartbeat) -> list | None:
        cfg = ProfileReviewConfig.default()
        now = datetime.now(timezone.utc)
        strategy_name, user_prompt = self.reviewer.resolve_strategy_name_prompt(
            run["strategy_id"], self.strategy_store
        )
        run["policy"] = {"name": strategy_name, "prompt": user_prompt}
        run["version"] = run["base_version"]

        working = to_working_profile(self.reviewer.fetch_old_profile(run["user_id"], run["strategy_id"], run["base_version"]))
        changed = False
        batch_size = max(1, cfg.batch_size)
        for start in range(0, len(run["inputs"]), batch_size):
            heartbeat.check()
            batch = run["inputs"][start : start + batch_size]
            extract_prompt = build_extract_prompt_safe(
                self.reviewer.format_memories(batch), user_prompt, cfg.constraints
            )
            p_list = call_with_parse_retry(
                self.reviewer._call_llm, extract_prompt, parse_extract_result,
                max_retries=cfg.retries, operation_name=f"Profile EXTRACT {run['strategy_id']}",
            )
            if not p_list:
                continue
            compare_prompt = build_compare_prompt_safe(
                self.reviewer.format_profile_json(working), p_list, user_prompt, cfg.constraints
            )
            compared = call_with_parse_retry(
                self.reviewer._call_llm, compare_prompt, parse_compare_result,
                max_retries=cfg.retries, operation_name=f"Profile COMPARE {run['strategy_id']}",
                is_empty=lambda r: not (r.get("updated") or r.get("added")),
            )
            added = _resolve_added_refs(compared["added"], p_list)
            updated = compared["updated"]
            if updated or added:
                changed = True
            apply_profile_updates(working, updated, added, now)
            compact_working_profile(working, now, cfg.category_limits, cfg.total_limit)
        return finalize_entries(working_to_entries(working), now, cfg.category_limits, cfg.total_limit) if changed else None

    def _publish(self, run, entries, heartbeat) -> None:
        if entries is None:
            heartbeat.check()
            self.tasks.materialize(run, [])
            self.tasks.publish(run, noop=True)
            return
        now = datetime.now(timezone.utc).isoformat()
        manifest = []
        for index, entry in enumerate(entries):
            heartbeat.check()
            payload = {k: v for k, v in entry.items() if k != "id"}
            payload.setdefault("mention_count", 0)
            payload.setdefault("heat_score", 0.0)
            payload.update(
                user_id=run["user_id"],
                strategy_id=run["strategy_id"],
                strategy_name=run["policy"]["name"],
                version=run["base_version"] + 1,
                snapshot_id=run["review_id"],
                created_at=now,
            )
            value = payload.get("updated_at")
            payload["updated_at"] = value.isoformat() if isinstance(value, datetime) else value or now
            pid = str(uuid.uuid4())
            vector = self.reviewer.embedding_model.embed(payload["data"], "add")
            manifest.append({"id": pid, "metadata": payload, "vectors": vector})
        heartbeat.check()
        self.tasks.materialize(run, manifest)
        if manifest:
            self.store.upsert(
                [m["id"] for m in manifest],
                [m["vectors"] for m in manifest],
                [m["metadata"] for m in manifest],
            )
        heartbeat.check()
        self.tasks.publish(run)

    def trigger(self, user_id: str, strategy_id: str, inputs: list, operation: str = None) -> str:
        """登记一个就绪事件(新记忆作为 inputs),返回 event_id。"""
        op = operation or uuid.uuid4().hex
        return self.tasks.register(user_id, strategy_id, op, inputs, ready=True, event_type="MANUAL_REVIEW")


def build_extract_prompt_safe(new_records, user_prompt, constraints):
    from .prompts import build_extract_prompt

    return build_extract_prompt(new_records=new_records, user_prompt=user_prompt, constraints=constraints)


def build_compare_prompt_safe(old_profile, p_list, user_prompt, constraints):
    from .prompts import build_compare_prompt
    import json

    return build_compare_prompt(
        old_profile=old_profile,
        p_list=json.dumps(p_list, ensure_ascii=False),
        user_prompt=user_prompt,
        constraints=constraints,
    )
