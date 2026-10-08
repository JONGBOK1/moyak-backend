from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.consult import shop
from src.consult.db import get_db

router = APIRouter(prefix="/vending", tags=["shop"])


class ProductResponse(BaseModel):
    id: str
    item_seq: str
    item_name: str
    company: str
    category: str
    badge: str | None
    price: int
    stock: int

    class Config:
        from_attributes = True


class OrderItemRequest(BaseModel):
    product_id: str = Field(..., min_length=1)
    quantity: int = Field(..., ge=1)


class CreateOrderRequest(BaseModel):
    items: list[OrderItemRequest] = Field(..., min_length=1)


class OrderItemResponse(BaseModel):
    item_name: str
    unit_price: int
    quantity: int

    class Config:
        from_attributes = True


class OrderResponse(BaseModel):
    id: str
    machine_id: str
    status: str
    total_amount: int
    created_at: datetime
    paid_at: datetime | None
    dispensed_at: datetime | None
    items: list[OrderItemResponse]

    class Config:
        from_attributes = True


@router.get("/machines/{machine_id}/products", response_model=list[ProductResponse])
def list_products(machine_id: str, category: str | None = None, db: Session = Depends(get_db)) -> list[ProductResponse]:
    """의약외품 즉시구매 목록 (본인인증 불필요). 상품이 없으면 데모 카탈로그로 자동 채워진다."""
    return shop.list_products(db, machine_id=machine_id, category=category)


@router.post("/machines/{machine_id}/orders", response_model=OrderResponse)
def create_order(machine_id: str, payload: CreateOrderRequest, db: Session = Depends(get_db)) -> OrderResponse:
    """장바구니 확정 -> 주문 생성 + 재고 차감. 결제 전 단계."""
    try:
        return shop.create_order(db, machine_id=machine_id, items=[item.model_dump() for item in payload.items])
    except shop.NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except shop.InvalidStateError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/orders/{order_id}", response_model=OrderResponse)
def get_order(order_id: str, db: Session = Depends(get_db)) -> OrderResponse:
    try:
        return shop.get_order(db, order_id=order_id)
    except shop.NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/orders/{order_id}/pay", response_model=OrderResponse)
def pay_order(order_id: str, db: Session = Depends(get_db)) -> OrderResponse:
    """실제 결제 연동 없이 모의 결제 처리."""
    try:
        return shop.pay_order(db, order_id=order_id)
    except shop.NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except shop.InvalidStateError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/orders/{order_id}/dispense", response_model=OrderResponse)
def dispense_order(order_id: str, db: Session = Depends(get_db)) -> OrderResponse:
    try:
        return shop.dispense_order(db, order_id=order_id)
    except shop.NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except shop.InvalidStateError as e:
        raise HTTPException(status_code=409, detail=str(e))
