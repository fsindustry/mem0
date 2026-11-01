"""digests 话题场景路由:seal/aggregate/scenes/feedback/settings。"""

from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..auth import get_runtime
from ..runtime.product import ProductRuntime

router = APIRouter(prefix="/v1/digests", tags=["digests"])


class SealRequest(BaseModel):
    owner_type: str = "user"
    owner_id: str
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    run_id: Optional[str] = None
    messages: List[str]
    memory_ids: List[str] = []


class AggregateRequest(BaseModel):
    owner_type: str = "user"
    owner_id: str


class SceneSearchRequest(BaseModel):
    query: str
    owner_type: str = "user"
    owner_id: str
    top_k: int = 10


class FeedbackCreateRequest(BaseModel):
    content: str


class SettingsUpsertRequest(BaseModel):
    memory_range: str = "LAST_3_MONTHS"
    schedule_time: Optional[Any] = None


@router.post("/seal", summary="Seal messages into a membox (topic)")
def seal_membox(req: SealRequest, runtime: ProductRuntime = Depends(get_runtime)):
    from ..digests.membox_handler import BoxSnapshot

    box = BoxSnapshot(
        owner_type=req.owner_type, owner_id=req.owner_id,
        user_id=req.user_id or (req.owner_id if req.owner_type == "user" else None),
        agent_id=req.agent_id or (req.owner_id if req.owner_type == "agent" else None),
        run_id=req.run_id, messages=req.messages, memory_ids=req.memory_ids,
    )
    box_id = runtime.membox_seal_handler.handle(req.owner_id, box.to_dict())
    if box_id is None:
        raise HTTPException(status_code=422, detail="Seal produced no membox (empty messages or embed failed)")
    return {"box_id": box_id}


@router.post("/aggregate", summary="Manually trigger memscene aggregation for an owner")
def aggregate(req: AggregateRequest, runtime: ProductRuntime = Depends(get_runtime)):
    runtime.digests_overall_handler.aggregate(req.owner_type, req.owner_id)
    return {"status": "ok", "owner_type": req.owner_type, "owner_id": req.owner_id}


@router.post("/scenes/search", summary="Search scenes (memscene)")
def search_scenes(req: SceneSearchRequest, runtime: ProductRuntime = Depends(get_runtime)):
    vector = runtime.facts.embedding_model.embed(req.query, "add")
    from ..digests.scene_search import scene_search

    results = scene_search(vector, runtime.memscene_store, req.owner_type, req.owner_id, limit=req.top_k)
    return {"results": [{"id": r["id"], "memory": r["payload"].get("content", ""), "scene_id": r["payload"].get("scene_id", ""), "scene_name": r["payload"].get("scene_name", ""), "score": r["score"], "metadata": r["payload"]} for r in results]}


@router.get("/scenes", summary="List scenes for an owner")
def list_scenes(
    runtime: ProductRuntime = Depends(get_runtime),
    owner_type: str = Query("user"),
    owner_id: str = Query(...),
    top_k: int = Query(50),
):
    items = runtime.memscene_store.list(filters={"owner_id": owner_id, "owner_type": owner_type}, limit=top_k)
    return {"results": [{"id": i["id"], "scene_id": i["payload"].get("scene_id", ""), "scene_name": i["payload"].get("scene_name", ""), "content": i["payload"].get("content", ""), "version_id": i["payload"].get("version_id", "")} for i in items]}


@router.get("/memboxes", summary="List memboxes for an owner")
def list_memboxes(
    runtime: ProductRuntime = Depends(get_runtime),
    user_id: Optional[str] = Query(None),
    agent_id: Optional[str] = Query(None),
    run_id: Optional[str] = Query(None),
    top_k: int = Query(50),
):
    filters: dict = {}
    if user_id:
        filters["user_id"] = user_id
    if agent_id:
        filters["agent_id"] = agent_id
    if run_id:
        filters["run_id"] = run_id
    items = runtime.membox_store.list(filters=filters or None, limit=top_k)
    return {"results": [{"id": i["id"], "topic": i["payload"].get("topic", ""), "content": i["payload"].get("content", "")} for i in items]}


# --- feedback ---
@router.get("/feedback", summary="List feedback for an owner")
def list_feedback(runtime: ProductRuntime = Depends(get_runtime), owner_type: str = Query("user"), owner_id: str = Query(...)):
    return {"results": runtime.digests_feedback_store.list(owner_type, owner_id)}


@router.post("/feedback", summary="Create feedback for an owner")
def create_feedback(req: FeedbackCreateRequest, runtime: ProductRuntime = Depends(get_runtime), owner_type: str = Query("user"), owner_id: str = Query(...)):
    fid = runtime.digests_feedback_store.create(owner_type, owner_id, req.content)
    return {"feedback_id": fid}


@router.delete("/feedback/{feedback_id}", summary="Delete feedback")
def delete_feedback(feedback_id: str, runtime: ProductRuntime = Depends(get_runtime)):
    if not runtime.digests_feedback_store.delete(feedback_id):
        raise HTTPException(status_code=404, detail="Feedback not found")
    return {"message": "deleted"}


# --- settings ---
@router.get("/settings", summary="Get digests settings for an owner")
def get_settings(runtime: ProductRuntime = Depends(get_runtime), owner_type: str = Query("user"), owner_id: str = Query(...)):
    cfg = runtime.digests_settings_store.get(owner_type, owner_id)
    return cfg or {"memory_range": "LAST_3_MONTHS", "schedule_time": None, "digest_version_chain": []}


@router.put("/settings", summary="Upsert digests settings for an owner")
def upsert_settings(req: SettingsUpsertRequest, runtime: ProductRuntime = Depends(get_runtime), owner_type: str = Query("user"), owner_id: str = Query(...)):
    runtime.digests_settings_store.upsert(owner_type, owner_id, req.memory_range, req.schedule_time)
    return runtime.digests_settings_store.get(owner_type, owner_id)


@router.get("/versions", summary="List digest version chain for an owner")
def list_versions(runtime: ProductRuntime = Depends(get_runtime), owner_type: str = Query("user"), owner_id: str = Query(...)):
    chain = runtime.digests_settings_store.get_version_chain(owner_type, owner_id)
    overviews = runtime.memscene_store.get_many(chain) if chain else {}
    return {"results": [{"id": cid, "content": overviews[cid]["payload"].get("content", "") if cid in overviews else ""} for cid in chain]}
