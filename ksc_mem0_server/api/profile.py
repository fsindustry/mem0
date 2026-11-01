"""画像记忆路由:检索画像 + 手动触发画像生成。"""

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ..auth import get_runtime
from ..runtime.product import ProductRuntime

router = APIRouter(prefix="/v1/profile", tags=["profile"])


class ProfileSearchRequest(BaseModel):
    query: str
    user_id: str
    strategy_id: Optional[str] = None
    top_k: int = 10


class ProfileTriggerRequest(BaseModel):
    user_id: str
    strategy_id: str = "default"
    inputs: list[dict[str, Any]]


@router.post("/search", summary="Search profile memories")
def search_profile(req: ProfileSearchRequest, runtime: ProductRuntime = Depends(get_runtime)):
    vector = runtime.facts.embedding_model.embed(req.query, "add")
    filters: dict[str, Any] = {"user_id": req.user_id}
    if req.strategy_id:
        filters["strategy_id"] = req.strategy_id
    results = runtime.profile_store.search(vector, filters=filters, limit=req.top_k)
    return {"results": [{"id": r["id"], "memory": r["payload"].get("data", ""), "score": r["score"], "metadata": r["payload"]} for r in results]}


@router.get("", summary="List profile memories")
def list_profile(
    runtime: ProductRuntime = Depends(get_runtime),
    user_id: str = Query(...),
    strategy_id: Optional[str] = Query(None),
    top_k: int = Query(100),
):
    filters: dict[str, Any] = {"user_id": user_id}
    if strategy_id:
        filters["strategy_id"] = strategy_id
    items = runtime.profile_store.list(filters=filters, limit=top_k)
    return {"results": [{"id": i["id"], "memory": i["payload"].get("data", ""), "metadata": i["payload"]} for i in items]}


@router.post("/trigger", summary="Manually trigger profile generation")
def trigger_profile(req: ProfileTriggerRequest, runtime: ProductRuntime = Depends(get_runtime)):
    event_id = runtime.trigger_profile(req.user_id, req.strategy_id, req.inputs)
    return {"event_id": event_id}
