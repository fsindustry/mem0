"""摘要记忆路由:检索摘要 + 手动触发摘要生成。"""

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ..auth import get_runtime
from ..runtime.product import ProductRuntime

router = APIRouter(prefix="/v1/summary", tags=["summary"])


class SummarySearchRequest(BaseModel):
    query: str
    user_id: str
    run_id: Optional[str] = None
    top_k: int = 10


class SummaryTriggerRequest(BaseModel):
    user_id: str
    run_id: str
    inputs: list[dict[str, Any]]


@router.post("/search", summary="Search summary memories")
def search_summary(req: SummarySearchRequest, runtime: ProductRuntime = Depends(get_runtime)):
    vector = runtime.facts.embedding_model.embed(req.query, "add")
    filters: dict[str, Any] = {"user_id": req.user_id}
    if req.run_id:
        filters["run_id"] = req.run_id
    results = runtime.summary_store.search(vector, filters=filters, limit=req.top_k)
    return {"results": [{"id": r["id"], "memory": r["payload"].get("data", ""), "section": r["payload"].get("section", ""), "score": r["score"], "metadata": r["payload"]} for r in results]}


@router.get("", summary="List summary memories")
def list_summary(
    runtime: ProductRuntime = Depends(get_runtime),
    user_id: str = Query(...),
    run_id: Optional[str] = Query(None),
    top_k: int = Query(100),
):
    filters: dict[str, Any] = {"user_id": user_id}
    if run_id:
        filters["run_id"] = run_id
    items = runtime.summary_store.list(filters=filters, limit=top_k)
    return {"results": [{"id": i["id"], "memory": i["payload"].get("data", ""), "section": i["payload"].get("section", ""), "metadata": i["payload"]} for i in items]}


@router.post("/trigger", summary="Manually trigger summary generation")
def trigger_summary(req: SummaryTriggerRequest, runtime: ProductRuntime = Depends(get_runtime)):
    event_id = runtime.trigger_summary(req.user_id, req.run_id, req.inputs)
    return {"event_id": event_id}
