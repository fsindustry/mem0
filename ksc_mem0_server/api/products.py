"""产品登记路由:显式预登记 product(可选,因首次访问会懒建)+ 列表/状态。

P0 提供显式 POST 便于测试与配置 overrides;GET 列出已登记 product。
"""

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..db import get_db
from ..models import Product
from ..runtime.schema import create_product_database

router = APIRouter(prefix="/v1/products", tags=["products"])


class ProductRegisterRequest(BaseModel):
    product_id: str
    config_overrides: Optional[dict[str, Any]] = None


@router.post("", summary="Register a product (creates its PG database)")
def register_product(req: ProductRegisterRequest, db=Depends(get_db)):
    existing = db.get(Product, req.product_id)
    if existing is not None:
        raise HTTPException(status_code=409, detail="Product already registered")
    db_name = create_product_database(req.product_id)
    product = Product(
        product_id=req.product_id,
        db_name=db_name,
        status="active",
        config_overrides=req.config_overrides,
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return {"product_id": product.product_id, "db_name": product.db_name, "status": product.status}


@router.get("", summary="List registered products")
def list_products(db=Depends(get_db)):
    rows = db.scalars(select(Product).order_by(Product.created_at.desc())).all()
    return {
        "results": [
            {"product_id": p.product_id, "db_name": p.db_name, "status": p.status}
            for p in rows
        ]
    }


@router.get("/{product_id}", summary="Product status")
def product_status(product_id: str, db=Depends(get_db)):
    p = db.get(Product, product_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Product not registered")
    return {
        "product_id": p.product_id,
        "db_name": p.db_name,
        "status": p.status,
        "config_overrides": p.config_overrides,
    }
