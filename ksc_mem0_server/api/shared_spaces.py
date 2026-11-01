"""shared_space 路由:空间/成员/规则/共享记忆检索/触发抽取。"""

from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..auth import get_runtime
from ..runtime.product import ProductRuntime
from ..shared_space.store import normalize_member_type

router = APIRouter(prefix="/v1/shared_spaces", tags=["shared_space"])


# --- 请求模型 ---
class SpaceCreateRequest(BaseModel):
    name: str
    instance_id: str = ""
    instance_name: str = ""
    extract_enabled: bool = False
    extract_prompt: Optional[str] = None
    token_threshold: Optional[int] = None


class SpaceNameUpdate(BaseModel):
    name: str


class SpacePromptUpdate(BaseModel):
    extract_prompt: str


class SpaceEnableUpdate(BaseModel):
    extract_enabled: bool


class MemberBatch(BaseModel):
    members: List[Tuple[str, str]]  # (member_id, member_type)


class RuleCreateRequest(BaseModel):
    content: str


class RuleUpdateRequest(BaseModel):
    content: str


class SharedSearchRequest(BaseModel):
    query: str
    top_k: int = 10


def _require_space(store, space_id: str) -> dict:
    space = store.get_space(space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Space not found")
    return space


# --- 空间 ---
@router.post("", summary="Create a shared space")
def create_space(req: SpaceCreateRequest, runtime: ProductRuntime = Depends(get_runtime)):
    from ..shared_space import DuplicateSpaceNameError

    try:
        space = runtime.shared_space_store.create_space(
            req.name, req.instance_id, req.instance_name, req.extract_enabled, req.extract_prompt, req.token_threshold
        )
    except DuplicateSpaceNameError:
        raise HTTPException(status_code=409, detail="Space name already exists")
    return space


@router.get("", summary="List shared spaces")
def list_spaces(runtime: ProductRuntime = Depends(get_runtime), text: str = Query(""), offset: int = Query(0), limit: int = Query(100)):
    total, spaces = runtime.shared_space_store.list_spaces(text, offset, limit)
    return {"total": total, "results": spaces}


@router.get("/{space_id}", summary="Get a shared space")
def get_space(space_id: str, runtime: ProductRuntime = Depends(get_runtime)):
    return _require_space(runtime.shared_space_store, space_id)


@router.put("/{space_id}/name", summary="Rename space")
def rename_space(space_id: str, req: SpaceNameUpdate, runtime: ProductRuntime = Depends(get_runtime)):
    _require_space(runtime.shared_space_store, space_id)
    runtime.shared_space_store.update_name(space_id, req.name)
    return runtime.shared_space_store.get_space(space_id)


@router.put("/{space_id}/prompt", summary="Update extract prompt")
def update_prompt(space_id: str, req: SpacePromptUpdate, runtime: ProductRuntime = Depends(get_runtime)):
    _require_space(runtime.shared_space_store, space_id)
    runtime.shared_space_store.update_prompt(space_id, req.extract_prompt)
    return runtime.shared_space_store.get_space(space_id)


@router.put("/{space_id}/enabled", summary="Enable/disable extraction")
def set_enabled(space_id: str, req: SpaceEnableUpdate, runtime: ProductRuntime = Depends(get_runtime)):
    _require_space(runtime.shared_space_store, space_id)
    runtime.shared_space_store.set_enabled(space_id, req.extract_enabled)
    return runtime.shared_space_store.get_space(space_id)


@router.delete("/{space_id}", summary="Delete a shared space")
def delete_space(space_id: str, runtime: ProductRuntime = Depends(get_runtime)):
    _require_space(runtime.shared_space_store, space_id)
    runtime.shared_vector_store.delete_by_space(space_id)
    runtime.shared_space_store.delete_space(space_id)
    return {"message": "deleted", "space_id": space_id}


@router.post("/{space_id}/trigger", summary="Manually trigger extraction")
def trigger_extraction(space_id: str, runtime: ProductRuntime = Depends(get_runtime)):
    _require_space(runtime.shared_space_store, space_id)
    ok = runtime.shared_space_registrar.trigger_space(space_id)
    return {"triggered": ok, "space_id": space_id}


# --- 成员 ---
@router.post("/{space_id}/members", summary="Add members")
def add_members(space_id: str, req: MemberBatch, runtime: ProductRuntime = Depends(get_runtime)):
    _require_space(runtime.shared_space_store, space_id)
    validated = [(m[0], normalize_member_type(m[1])) for m in req.members]
    if any(v is None for _, v in validated):
        raise HTTPException(status_code=400, detail="Invalid member_type (user/agent)")
    added = runtime.shared_space_store.add_members(space_id, [v for v in validated if v[1]])  # type: ignore[arg-type]
    return {"added": [{"member_id": m, "member_type": t} for m, t in added]}


@router.delete("/{space_id}/members", summary="Remove members")
def remove_members(space_id: str, req: MemberBatch, runtime: ProductRuntime = Depends(get_runtime)):
    _require_space(runtime.shared_space_store, space_id)
    validated = [(m[0], normalize_member_type(m[1])) for m in req.members]
    n = runtime.shared_space_store.remove_members(space_id, [(m, t) for m, t in validated if t])  # type: ignore[arg-type]
    return {"removed": n}


@router.get("/{space_id}/members", summary="List members")
def list_members(space_id: str, runtime: ProductRuntime = Depends(get_runtime), offset: int = Query(0), limit: int = Query(100)):
    _require_space(runtime.shared_space_store, space_id)
    total, members = runtime.shared_space_store.list_members(space_id, offset, limit)
    return {"total": total, "results": members}


@router.get("/members/spaces", summary="List spaces a member belongs to")
def list_member_spaces(runtime: ProductRuntime = Depends(get_runtime), member_id: str = Query(...), member_type: str = Query(...)):
    mt = normalize_member_type(member_type)
    if not mt:
        raise HTTPException(status_code=400, detail="Invalid member_type")
    return {"results": runtime.member_catalog_store.list_member_spaces(member_id, mt)}


# --- 规则 ---
@router.post("/{space_id}/rules", summary="Add a rule")
def add_rule(space_id: str, req: RuleCreateRequest, runtime: ProductRuntime = Depends(get_runtime)):
    _require_space(runtime.shared_space_store, space_id)
    return runtime.shared_space_store.add_rule(space_id, req.content)


@router.get("/{space_id}/rules", summary="List rules")
def list_rules(space_id: str, runtime: ProductRuntime = Depends(get_runtime), offset: int = Query(0), limit: int = Query(100)):
    _require_space(runtime.shared_space_store, space_id)
    total, rules = runtime.shared_space_store.list_rules(space_id, offset, limit)
    return {"total": total, "results": rules}


@router.put("/{space_id}/rules/{rule_id}", summary="Update a rule")
def update_rule(space_id: str, rule_id: str, req: RuleUpdateRequest, runtime: ProductRuntime = Depends(get_runtime)):
    _require_space(runtime.shared_space_store, space_id)
    if not runtime.shared_space_store.update_rule(space_id, rule_id, req.content):
        raise HTTPException(status_code=404, detail="Rule not found")
    return runtime.shared_space_store.get_rule(space_id, rule_id)


@router.delete("/{space_id}/rules/{rule_id}", summary="Delete a rule")
def delete_rule(space_id: str, rule_id: str, runtime: ProductRuntime = Depends(get_runtime)):
    _require_space(runtime.shared_space_store, space_id)
    if not runtime.shared_space_store.delete_rule(space_id, rule_id):
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"message": "deleted", "rule_id": rule_id}


# --- 共享记忆检索 ---
@router.post("/{space_id}/memories/search", summary="Search shared memories")
def search_shared_memories(space_id: str, req: SharedSearchRequest, runtime: ProductRuntime = Depends(get_runtime)):
    _require_space(runtime.shared_space_store, space_id)
    vector = runtime.facts.embedding_model.embed(req.query, "add")
    results = runtime.shared_vector_store.search(vector, space_id=space_id, limit=req.top_k)
    return {"results": [{"id": r["id"], "memory": r["payload"].get("content", ""), "score": r["score"], "metadata": r["payload"]} for r in results]}


@router.get("/{space_id}/memories", summary="List shared memories")
def list_shared_memories(space_id: str, runtime: ProductRuntime = Depends(get_runtime), top_k: int = Query(100)):
    _require_space(runtime.shared_space_store, space_id)
    items = runtime.shared_vector_store.list(space_id, limit=top_k)
    return {"results": [{"id": i["id"], "memory": i["payload"].get("content", ""), "metadata": i["payload"]} for i in items]}
