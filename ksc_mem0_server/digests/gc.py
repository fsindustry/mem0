"""MemSceneGC:清理 range=ALL 无 membox 指向的过期 memscene。

迁移自参考库 digests/memscene_gc.py,简化:interval 而非 croniter。
判据:range=ALL + updated_at < cutoff + scene_id 不被任何 membox 指向。
membox 的 user_scene_ids/agent_scene_ids 反查(P3b 未回写,GC 暂只按过期清,attach 回写留 P5)。
"""

import logging
import threading
from datetime import datetime, timedelta, timezone

from .vector_store import MemBoxStore, MemSceneStore

logger = logging.getLogger(__name__)

DEFAULT_GC_MAX_AGE_SECONDS = 1800


class MemSceneGC:
    def __init__(self, membox_store: MemBoxStore, memscene_store: MemSceneStore, interval: float = 3600.0) -> None:
        self._membox_store = membox_store
        self._memscene_store = memscene_store
        self._interval = interval

    def gc_once(self, max_age: int = DEFAULT_GC_MAX_AGE_SECONDS) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max_age)).isoformat()
        # 反向扫 membox 收集被引用 scene_id(P3b 未回写 scene_ids,此集合常为空,GC 暂只按过期清)
        referenced = self._collect_referenced_scene_ids()
        deleted = 0
        for scan_range in ("ALL", "ALL_SNAPSHOT"):
            try:
                candidates = self._memscene_store.list_stale_all(scan_range, cutoff)
            except Exception:
                logger.exception("MemSceneGC list_stale_all failed range=%s", scan_range)
                continue
            for item in candidates:
                payload = item.get("payload") or {}
                scene_id = payload.get("scene_id") or ""
                # referenced 含被 membox 指向的 scene_id;all 总览(scene_id="all")不被 membox 指向,可清
                if not scene_id or scene_id in referenced:
                    continue
                try:
                    if self._memscene_store.delete(item["id"]):
                        deleted += 1
                except Exception:
                    logger.warning("MemSceneGC delete failed id=%s", item.get("id"))
        if deleted:
            logger.info("MemSceneGC deleted %d stale memscene", deleted)
        return deleted

    def _collect_referenced_scene_ids(self) -> set:
        referenced: set = set()
        # P3b 未回写 membox 的 scene_ids,扫 membox 全量收集(P5 attach 后生效)
        try:
            for key in ("user_scene_ids", "agent_scene_ids"):
                # membox list 需 scope,此处无法全量扫;P5 attach 回写后按 owner 扫
                pass
        except Exception:
            logger.exception("MemSceneGC collect referenced failed")
        return referenced

    def run_forever(self, stop_event: threading.Event) -> None:
        while not stop_event.wait(self._interval):
            try:
                self.gc_once()
            except Exception:
                logger.exception("MemSceneGC round failed")
