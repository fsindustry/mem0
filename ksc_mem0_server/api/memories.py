"""事实记忆路由:对齐 mem0 Memory 公共 API(add/search/get/get_all/update/delete/delete_all/history)。

注意 v2.2.1 语义:
  - add / delete_all 的 user_id/agent_id/run_id 是顶层参数;
  - search / get_all 用 filters dict 承载 user_id/agent_id/run_id + 元数据过滤,用 top_k(非 limit)。
API 层接受顶层 user_id/agent_id/run_id(对调用方友好),内部组装为 filters。
"""

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ..auth import get_runtime
from ..runtime.product import ProductRuntime

router = APIRouter(tags=["memories"])


class MessageIn(BaseModel):
    role: str
    content: str
    name: Optional[str] = None


class AddRequest(BaseModel):
    messages: list[MessageIn] | str
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    run_id: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None
    infer: bool = True
    memory_type: Optional[str] = None
    prompt: Optional[str] = None


class SearchRequest(BaseModel):
    query: str
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    run_id: Optional[str] = None
    top_k: int = 20
    filters: Optional[dict[str, Any]] = None


class UpdateRequest(BaseModel):
    data: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


def _scope_filters(
    user_id: Optional[str],
    agent_id: Optional[str],
    run_id: Optional[str],
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """把顶层 user_id/agent_id/run_id 合并进 filters dict(mem0 v2.2.1 语义)。"""
    filters: dict[str, Any] = {}
    if user_id is not None:
        filters["user_id"] = user_id
    if agent_id is not None:
        filters["agent_id"] = agent_id
    if run_id is not None:
        filters["run_id"] = run_id
    if extra:
        filters.update(extra)
    return filters


def _messages_payload(messages: list[MessageIn] | str) -> list[dict[str, Any]] | str:
    if isinstance(messages, str):
        return messages
    return [m.model_dump(exclude_none=True) for m in messages]


@router.post("/memories", summary="Add memories")
def add_memories(req: AddRequest, runtime: ProductRuntime = Depends(get_runtime)):
    return runtime.facts.add(
        _messages_payload(req.messages),
        user_id=req.user_id,
        agent_id=req.agent_id,
        run_id=req.run_id,
        metadata=req.metadata,
        infer=req.infer,
        memory_type=req.memory_type,
        prompt=req.prompt,
    )


@router.post("/search", summary="Search memories")
def search_memories(req: SearchRequest, runtime: ProductRuntime = Depends(get_runtime)):
    filters = _scope_filters(req.user_id, req.agent_id, req.run_id, req.filters)
    return runtime.facts.search(req.query, top_k=req.top_k, filters=filters)


@router.get("/memories", summary="List memories")
def list_memories(
    runtime: ProductRuntime = Depends(get_runtime),
    user_id: Optional[str] = Query(None),
    agent_id: Optional[str] = Query(None),
    run_id: Optional[str] = Query(None),
    top_k: int = Query(20),
):
    filters = _scope_filters(user_id, agent_id, run_id)
    return runtime.facts.get_all(top_k=top_k, filters=filters)


@router.get("/memories/{memory_id}", summary="Get a memory")
def get_memory(memory_id: str, runtime: ProductRuntime = Depends(get_runtime)):
    return runtime.facts.get(memory_id)


@router.put("/memories/{memory_id}", summary="Update a memory")
def update_memory(memory_id: str, req: UpdateRequest, runtime: ProductRuntime = Depends(get_runtime)):
    return runtime.facts.update(memory_id, data=req.data, metadata=req.metadata)


@router.delete("/memories/{memory_id}", summary="Delete a memory")
def delete_memory(memory_id: str, runtime: ProductRuntime = Depends(get_runtime)):
    return runtime.facts.delete(memory_id)


@router.delete("/memories", summary="Delete all memories in scope")
def delete_all_memories(
    runtime: ProductRuntime = Depends(get_runtime),
    user_id: Optional[str] = Query(None),
    agent_id: Optional[str] = Query(None),
    run_id: Optional[str] = Query(None),
):
    return runtime.facts.delete_all(user_id=user_id, agent_id=agent_id, run_id=run_id)


@router.get("/memories/{memory_id}/history", summary="Memory change history")
def memory_history(memory_id: str, runtime: ProductRuntime = Depends(get_runtime)):
    return {"results": runtime.facts.history(memory_id)}
