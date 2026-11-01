"""SummaryReviewHandler:异步 consumer 的摘要任务处理器。"""

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

SUMMARY_CATEGORY = "summary"
SUMMARY_TASK_TYPE = "review"


class SummaryReviewHandler:
    def __init__(self, summary_runtime) -> None:
        self._runtime = summary_runtime

    def handle(self, item: Any, runtime: Any) -> None:
        summary_runtime = runtime.summary_runtime
        payload = item.payload or {}
        user_id = payload.get("user_id") or item.user_id
        run_id = payload.get("run_id") or "default"
        start = datetime.now(timezone.utc)
        try:
            summary_runtime.review(user_id, run_id)
        finally:
            elapsed = (datetime.now(timezone.utc) - start).total_seconds()
            logger.info("summary review done user=%s run=%s elapsed=%.3fs", user_id, run_id, elapsed)


def register_summary_handler(summary_runtime) -> None:
    from ..tasks.handlers import register_handler

    register_handler(SUMMARY_CATEGORY, SUMMARY_TASK_TYPE, SummaryReviewHandler(summary_runtime))
