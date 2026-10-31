"""策略管理路由:三类型 CRUD + 类型字典。"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import get_runtime
from ..runtime.product import ProductRuntime
from ..strategy import get_strategy_types
from ..strategy.store import STATUS_ACTIVE, STATUS_DISABLED

router = APIRouter(prefix="/v1/strategies", tags=["strategies"])


class StrategyCreateRequest(BaseModel):
    name: str
    strategy_type: str
    prompt: str


class StrategyUpdateRequest(BaseModel):
    name: Optional[str] = None
    prompt: Optional[str] = None


@router.get("/types", summary="List strategy types")
def list_strategy_types():
    return {"results": get_strategy_types()}


@router.post("", summary="Create a strategy")
def create_strategy(req: StrategyCreateRequest, runtime: ProductRuntime = Depends(get_runtime)):
    if req.strategy_type not in ("profile", "fact", "summary", "global"):
        raise HTTPException(status_code=400, detail="Invalid strategy_type")
    return runtime.strategy_store.create_strategy(req.name, req.strategy_type, req.prompt)


@router.get("", summary="List strategies")
def list_strategies(
    runtime: ProductRuntime = Depends(get_runtime),
    strategy_type: Optional[str] = None,
    include_deleted: bool = False,
):
    return {"results": runtime.strategy_store.list_strategies(strategy_type=strategy_type, include_deleted=include_deleted)}


@router.get("/{strategy_id}", summary="Get a strategy")
def get_strategy(strategy_id: str, runtime: ProductRuntime = Depends(get_runtime)):
    s = runtime.strategy_store.get_strategy(strategy_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return s


@router.put("/{strategy_id}", summary="Update a strategy")
def update_strategy(strategy_id: str, req: StrategyUpdateRequest, runtime: ProductRuntime = Depends(get_runtime)):
    if runtime.strategy_store.get_strategy(strategy_id) is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    runtime.strategy_store.update_strategy(strategy_id, name=req.name, prompt=req.prompt)
    return runtime.strategy_store.get_strategy(strategy_id)


@router.delete("/{strategy_id}", summary="Delete a strategy (soft)")
def delete_strategy(strategy_id: str, runtime: ProductRuntime = Depends(get_runtime)):
    if runtime.strategy_store.get_strategy(strategy_id) is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    runtime.strategy_store.delete_strategy(strategy_id)
    return {"message": "deleted", "id": strategy_id}


@router.post("/{strategy_id}/enable", summary="Enable a strategy")
def enable_strategy(strategy_id: str, runtime: ProductRuntime = Depends(get_runtime)):
    if runtime.strategy_store.get_strategy(strategy_id) is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    runtime.strategy_store.set_strategy_status(strategy_id, STATUS_ACTIVE)
    return runtime.strategy_store.get_strategy(strategy_id)


@router.post("/{strategy_id}/disable", summary="Disable a strategy")
def disable_strategy(strategy_id: str, runtime: ProductRuntime = Depends(get_runtime)):
    if runtime.strategy_store.get_strategy(strategy_id) is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    runtime.strategy_store.set_strategy_status(strategy_id, STATUS_DISABLED)
    return runtime.strategy_store.get_strategy(strategy_id)
