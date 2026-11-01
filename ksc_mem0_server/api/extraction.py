"""失败补偿路由:列出/重试/删除失败记录 + 手动触发扫描。"""

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import get_runtime
from ..runtime.product import ProductRuntime

router = APIRouter(prefix="/v1/extraction", tags=["extraction"])


def _get_scanner():
    from .. import app as _app

    return _app._scanner


@router.get("/failures", summary="List extraction failure records")
def list_failures(
    runtime: ProductRuntime = Depends(get_runtime),
    status: str | None = Query(None),
    limit: int = Query(100),
):
    return {"results": runtime.extraction_failure_store.list(status=status, limit=limit)}


@router.post("/failures/{failure_id}/retry", summary="Reset a DEAD failure to PENDING")
def retry_failure(failure_id: str, runtime: ProductRuntime = Depends(get_runtime)):
    ok = runtime.extraction_failure_store.reset_to_pending(failure_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Failure not found or not DEAD")
    return {"message": "reset", "id": failure_id}


@router.delete("/failures/{failure_id}", summary="Delete a failure record")
def delete_failure(failure_id: str, runtime: ProductRuntime = Depends(get_runtime)):
    ok = runtime.extraction_failure_store.delete(failure_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Failure not found")
    return {"message": "deleted", "id": failure_id}


@router.delete("/failures", summary="Batch delete terminal failure records")
def delete_terminal_failures(
    runtime: ProductRuntime = Depends(get_runtime),
    status: str = Query(..., description="SUCCEEDED or DEAD"),
):
    n = runtime.extraction_failure_store.delete_terminal(status)
    return {"deleted": n}


@router.post("/compensate", summary="Manually trigger compensation scan")
def trigger_compensate():
    scanner = _get_scanner()
    if scanner is None:
        raise HTTPException(status_code=503, detail="Scanner not initialized")
    n = scanner.scan_once()
    return {"handled": n}
