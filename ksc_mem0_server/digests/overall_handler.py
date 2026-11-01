"""DigestOverallHandler:跨会话场景聚合(drain pending membox → 分类 → 合并生成 memscene)。

迁移自参考库 digests/handler.py 的 _generate_overall + _classify_and_accumulate + _merge_and_persist_scene,
简化:
  - 仅 overall(range=ALL),跳过 day/range 中间场景
  - 跳过 _attach_scene_to_memboxes 回写 membox 的 scene_ids(留 P5,需主记忆 episode 结构)
  - 保留核心:drain pending_queue → _assign_membox(LLM 判归入/新建)→ 同 scene 累积达阈值合并
    → _merge_and_persist_scene(LLM 合并 + embed + upsert memscene,versioned=False)→ 收尾合并不足阈值
    → range=ALL 总览(scene_id="all",versioned=True,入版本链)
"""

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from ..profile.review import call_with_parse_retry
from .constants import now_iso, version_num
from .pending_queue import MemBoxPendingQueue
from .prompts import (
    build_digest_merge_prompt,
    build_membox_scene_assign_prompt,
    parse_scene_assign_result,
)
from .settings_store import DigestsSettingsStore
from .vector_store import MemBoxStore, MemSceneStore

logger = logging.getLogger(__name__)

OVERALL_CATEGORY = "digests_overall"
OVERALL_TASK_TYPE = "aggregate"
RANGE_ALL = "ALL"
DEFAULT_MERGE_THRESHOLD = 5


def _membox_ids(memboxes: List[Dict[str, Any]]) -> List[str]:
    return [m.get("id") for m in memboxes if m.get("id")]


class DigestOverallHandler:
    """overall 聚合处理器:drain pending membox → 分类 → 合并 → memscene + 版本链。"""

    def __init__(
        self,
        llm,
        embedding_model,
        membox_store: MemBoxStore,
        memscene_store: MemSceneStore,
        settings_store: DigestsSettingsStore,
        pending_queue: MemBoxPendingQueue,
        merge_threshold: int = DEFAULT_MERGE_THRESHOLD,
    ) -> None:
        self.llm = llm
        self.embedding_model = embedding_model
        self._membox_store = membox_store
        self._memscene_store = memscene_store
        self._settings_store = settings_store
        self._pending_queue = pending_queue
        self.merge_threshold = merge_threshold

    def handle(self, user_id: str, payload: Dict[str, Any]) -> None:
        """从 payload 取 owner_type/owner_id,drain pending 并聚合。"""
        owner_type = payload.get("owner_type", "user")
        owner_id = payload.get("owner_id", user_id)
        self.aggregate(owner_type, owner_id)

    def aggregate(self, owner_type: str, owner_id: str) -> None:
        """drain 该 owner 的 pending membox → 分类累积 → 合并落 memscene → 生成 all 总览。"""
        box_ids = self._pending_queue.drain(owner_type, owner_id)
        if not box_ids:
            return
        memboxes = [mb for mb in (self._membox_store.get(bid) for bid in box_ids) if mb is not None]
        if not memboxes:
            return
        logger.info("DigestOverall aggregate owner=%s/%s boxes=%d", owner_type, owner_id, len(memboxes))

        candidate_scenes = self._load_candidate_scenes(owner_type, owner_id)
        end_time = now_iso()
        pending: Dict[str, Dict[str, Any]] = {}

        for mb in memboxes:
            scene_ids, new_scene_name = self._assign_membox(mb, candidate_scenes)
            if not scene_ids:
                new_scene = {"scene_id": str(uuid.uuid4()), "scene_name": new_scene_name or "新场景", "content": ""}
                candidate_scenes.append(new_scene)
                scene_ids = [new_scene["scene_id"]]
            scene_ref_by_id = {s.get("scene_id"): s for s in candidate_scenes}
            for sid in scene_ids:
                entry = pending.setdefault(sid, {"scene_ref": scene_ref_by_id.get(sid), "memboxes": []})
                entry["memboxes"].append(mb)
                if len(entry["memboxes"]) >= self.merge_threshold:
                    self._merge_and_persist_scene(owner_type, owner_id, end_time, entry["scene_ref"], entry["memboxes"])
                    entry["memboxes"] = []

        # 收尾:不足阈值也合并
        for entry in pending.values():
            if entry["memboxes"]:
                self._merge_and_persist_scene(owner_type, owner_id, end_time, entry["scene_ref"], entry["memboxes"])

        # 生成 all 总览(合并所有场景的最新版本)
        self._generate_all_overview(owner_type, owner_id, end_time)

    def _load_candidate_scenes(self, owner_type: str, owner_id: str) -> List[Dict[str, Any]]:
        """取该 owner 现有场景(最新版本)作分类候选。"""
        scenes = self._memscene_store.list(filters={"owner_id": owner_id, "owner_type": owner_type}, limit=100)
        # 按 scene_id 取最新 version_id
        by_scene: Dict[str, Dict[str, Any]] = {}
        for s in scenes:
            payload = s.get("payload") or {}
            sid = payload.get("scene_id")
            if not sid or sid == "all":
                continue
            existing = by_scene.get(sid)
            if existing is None or version_num(payload.get("version_id", "")) > version_num(existing.get("version_id", "")):
                by_scene[sid] = payload
        return [{"scene_id": sid, "scene_name": p.get("scene_name", ""), "content": p.get("content", "")} for sid, p in by_scene.items()]

    def _call_llm(self, prompt: str) -> str:
        return self.llm.generate_response(messages=[{"role": "user", "content": prompt}], response_format={"type": "json_object"})

    def _call_llm_text(self, prompt: str) -> str:
        return self.llm.generate_response(messages=[{"role": "user", "content": prompt}])

    def _assign_membox(self, mb: Dict[str, Any], candidate_scenes: List[Dict[str, Any]]) -> tuple:
        payload = mb.get("payload") or {}
        topic = payload.get("topic", "")
        content = payload.get("content", "")
        scenes_json = json.dumps([{"scene_id": s["scene_id"], "scene_name": s["scene_name"]} for s in candidate_scenes], ensure_ascii=False)
        prompt = build_membox_scene_assign_prompt(topic, content, scenes_json)
        result = call_with_parse_retry(
            self._call_llm, prompt, parse_scene_assign_result,
            max_retries=2, operation_name="Scene assign", is_empty=lambda r: not r["scene_ids"] and not r["new_scene_name"],
        )
        scene_ids = [sid for sid in result["scene_ids"] if sid != "NEW"]
        new_name = result["new_scene_name"]
        if "NEW" in result["scene_ids"] and new_name:
            new_scene = {"scene_id": str(uuid.uuid4()), "scene_name": new_name, "content": ""}
            candidate_scenes.append(new_scene)
            scene_ids.append(new_scene["scene_id"])
        return scene_ids, new_name

    def _build_membox_scenes_text(self, memboxes: List[Dict[str, Any]]) -> str:
        parts = []
        for mb in memboxes:
            payload = mb.get("payload") or {}
            topic = payload.get("topic", "")
            content = payload.get("content", "")
            parts.append(f"[{topic}] {content}")
        return "\n".join(parts)

    def _merge_and_persist_scene(
        self, owner_type: str, owner_id: str, end_time: str, scene_ref: Optional[Dict[str, Any]], memboxes: List[Dict[str, Any]]
    ) -> Optional[str]:
        scene_id = (scene_ref or {}).get("scene_id") or str(uuid.uuid4())
        scene_name = (scene_ref or {}).get("scene_name") or scene_id
        scenes_text = self._build_membox_scenes_text(memboxes)
        # overall 累积:并入已有场景摘要
        existing = (scene_ref or {}).get("content") or ""
        if existing.strip():
            scenes_text = f"【已有场景摘要】\n{existing}\n\n【本批次新增】\n{scenes_text}"
        merge_prompt = build_digest_merge_prompt(old_content=existing, new_topic="", new_content=scenes_text, keywords=[])
        merged = self._call_llm_text(merge_prompt)
        if not merged or not merged.strip():
            logger.warning("Digest merge empty, skip scene_id=%s", scene_id)
            return None
        # versioned=False(overall 场景不递增 version_id,只 all 总览递增)
        record_id = self._persist_scene_record(
            owner_type, owner_id, scene_id, scene_name, merged, RANGE_ALL, end_time, versioned=False, membox_count=len(memboxes)
        )
        # 累积闭合:回写 scene_ref content
        if scene_ref is not None:
            scene_ref["content"] = merged
        return record_id

    def _persist_scene_record(
        self, owner_type, owner_id, scene_id, scene_name, content, range_str, end_time, versioned=False, membox_count=0
    ) -> Optional[str]:
        version_id = self._next_version_id(owner_type, owner_id, scene_id) if versioned else ""
        payload = {
            "owner_type": owner_type,
            "owner_id": owner_id,
            "scene_id": scene_id,
            "scene_name": scene_name,
            "content": content,
            "version_id": version_id,
            "status": "COMPLETED",
            "range": range_str,
            "membox_count": membox_count,
            "last_update_time": end_time,
        }
        try:
            vector = self.embedding_model.embed(content, "add")
        except Exception:
            logger.exception("memscene embed failed scene_id=%s", scene_id)
            return None
        record_id = str(uuid.uuid4())
        try:
            self._memscene_store.upsert([record_id], [vector], [payload])
        except Exception:
            logger.exception("memscene upsert failed record_id=%s", record_id)
            return None
        return record_id

    def _next_version_id(self, owner_type: str, owner_id: str, scene_id: str) -> str:
        """查同 (owner, scene_id) 最大 version_id +1。"""
        scenes = self._memscene_store.list(filters={"owner_id": owner_id, "owner_type": owner_type, "scene_id": scene_id}, limit=500)
        max_v = 0
        for s in scenes:
            payload = s.get("payload") or {}
            v = version_num(payload.get("version_id", ""))
            if v > max_v:
                max_v = v
        return str(max_v + 1)

    def _generate_all_overview(self, owner_type: str, owner_id: str, end_time: str) -> None:
        """合并所有场景最新版本 → all 总览(scene_id="all",versioned,入版本链)。"""
        scenes = self._memscene_store.list(filters={"owner_id": owner_id, "owner_type": owner_type}, limit=100)
        # 取每个 scene_id 最新版本
        by_scene: Dict[str, Dict[str, Any]] = {}
        for s in scenes:
            payload = s.get("payload") or {}
            sid = payload.get("scene_id")
            if not sid or sid == "all":
                continue
            existing = by_scene.get(sid)
            if existing is None or version_num(payload.get("version_id", "")) > version_num(existing.get("version_id", "")):
                by_scene[sid] = payload
        if not by_scene:
            return
        scenes_text = "\n\n".join(f"[{p.get('scene_name', '')}] {p.get('content', '')}" for p in by_scene.values())
        merge_prompt = build_digest_merge_prompt(old_content="", new_topic="全部场景总览", new_content=scenes_text, keywords=[])
        merged = self._call_llm_text(merge_prompt)
        if not merged or not merged.strip():
            return
        record_id = self._persist_scene_record(
            owner_type, owner_id, "all", "总览", merged, RANGE_ALL, end_time, versioned=True, membox_count=len(by_scene)
        )
        if record_id:
            # 入版本链(newest-first)
            chain = self._settings_store.get_version_chain(owner_type, owner_id)
            chain.insert(0, record_id)
            self._settings_store.update_version_chain(owner_type, owner_id, chain)


def register_overall_handler(handler: DigestOverallHandler) -> None:
    from ..tasks.handlers import register_handler

    register_handler(OVERALL_CATEGORY, OVERALL_TASK_TYPE, handler)
