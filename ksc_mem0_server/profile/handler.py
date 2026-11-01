"""ProfileReviewHandler:异步 consumer 的画像任务处理器。

handle(item, runtime) 从 item.payload 取 user_id/strategy_id,委托 ProfileRuntime.review。
try/finally 保证 gate.mark_done 清理 in-flight(无超时自愈,必须显式清理)。
"""

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

PROFILE_CATEGORY = "profile"
PROFILE_TASK_TYPE = "review"


class ProfileReviewHandler:
    def __init__(self, profile_runtime, enqueue_gate, pending_buffer=None) -> None:
        self._runtime = profile_runtime
        self._gate = enqueue_gate
        self._pending_buffer = pending_buffer

    def handle(self, item: Any, runtime: Any) -> None:
        # runtime 是 ProductRuntime;profile_runtime 在其上
        profile_runtime = runtime.profile_runtime
        payload = item.payload or {}
        user_id = payload.get("user_id") or item.user_id
        strategy_id = payload.get("strategy_id") or "default"
        start = datetime.now(timezone.utc)
        try:
            profile_runtime.review(user_id, strategy_id)
        finally:
            if self._pending_buffer is not None:
                try:
                    self._pending_buffer.finish_review(user_id)
                except Exception:
                    logger.exception("pending_buffer.finish_review failed user=%s", user_id)
            self._gate.mark_done(user_id, strategy_id)
            elapsed = (datetime.now(timezone.utc) - start).total_seconds()
            logger.info("profile review done user=%s strategy=%s elapsed=%.3fs", user_id, strategy_id, elapsed)


def register_profile_handler(profile_runtime, enqueue_gate, pending_buffer=None) -> None:
    from ..tasks.handlers import register_handler

    register_handler(
        PROFILE_CATEGORY, PROFILE_TASK_TYPE, ProfileReviewHandler(profile_runtime, enqueue_gate, pending_buffer)
    )
