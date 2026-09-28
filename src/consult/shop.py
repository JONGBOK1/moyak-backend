"""의약외품 즉시구매(본인인증 불필요) 흐름 - 상품 조회 -> 장바구니 주문 -> 결제 -> 수령.

키오스크 화면에서 로그인 없이 진행되는 경로라 사용자 계정과 연결되지 않는다.
결제는 실제 PG 연동이 없어 모의(mock) 처리한다.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.consult.models import KioskOrder, KioskOrderItem, OrderStatus, Product, VendingMachine

# 상품을 등록할 관리 화면이 아직 없어서, 자판기에 상품이 하나도 없으면
# Figma 디자인(kiosk-otc-list)에 나온 실제 품목으로 자동 채워 데모가 바로 되게 한다.
DEMO_CATALOG = [
    dict(item_seq="196500043", item_name="대일밴드 뉴 표준형 20매", company="대일화학", category="안대/밴드/기타", badge="상처 보호", price=3500, stock=30),
    dict(item_seq="202101234", item_name="메디폼 H뷰티", company="먼디파마", category="상처/연고", badge="상처 보호", price=4000, stock=20),
    dict(item_seq="202209876", item_name="KF94 보건용 마스크", company="크린앤사이언스", category="안대/밴드/기타", badge="마스크", price=3800, stock=50),
    dict(item_seq="199900001", item_name="리스테린 구강청결제", company="존슨앤드존슨", category="상처/연고", badge="구강 위생", price=6000, stock=15),
    dict(item_seq="200300002", item_name="알콜스왑", company="더가든오브내추럴", category="상처/연고", badge="소독용품", price=3000, stock=40),
    dict(item_seq="200500003", item_name="좋은느낌 생리대", company="깨끗한나라", category="안대/밴드/기타", badge="여성용품", price=4000, stock=25),
]


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class NotFoundError(Exception):
    pass


class InvalidStateError(Exception):
    pass


def _ensure_machine(db: Session, machine_id: str) -> VendingMachine:
    machine = db.get(VendingMachine, machine_id)
    if machine is None:
        machine = VendingMachine(id=machine_id, name=machine_id)
        db.add(machine)
        db.commit()
    return machine


def _seed_products_if_empty(db: Session, machine_id: str) -> None:
    exists = db.query(Product).filter(Product.machine_id == machine_id).first()
    if exists is not None:
        return
    for item in DEMO_CATALOG:
        db.add(Product(machine_id=machine_id, **item))
    db.commit()


def list_products(db: Session, machine_id: str, category: str | None = None) -> list[Product]:
    _ensure_machine(db, machine_id)
    _seed_products_if_empty(db, machine_id)
    query = db.query(Product).filter(Product.machine_id == machine_id)
    if category and category != "전체":
        query = query.filter(Product.category == category)
    return query.all()


def create_order(db: Session, machine_id: str, items: list[dict]) -> KioskOrder:
    """items: [{"product_id": ..., "quantity": ...}, ...]. 재고를 검증하고 즉시 차감한다."""
    if not items:
        raise InvalidStateError("장바구니가 비어 있습니다.")

    order = KioskOrder(id=str(uuid.uuid4()), machine_id=machine_id, status=OrderStatus.CART, total_amount=0)
    db.add(order)
    total = 0

    for entry in items:
        product = db.get(Product, entry["product_id"])
        if product is None or product.machine_id != machine_id:
            raise NotFoundError(f"상품을 찾을 수 없습니다: {entry['product_id']}")
        quantity = entry["quantity"]
        if quantity < 1:
            raise InvalidStateError(f"수량은 1개 이상이어야 합니다: {product.item_name}")
        if product.stock < quantity:
            raise InvalidStateError(f"재고가 부족합니다: {product.item_name} (남은 수량 {product.stock}개)")

        product.stock -= quantity
        line_total = product.price * quantity
        total += line_total
        db.add(
            KioskOrderItem(
                id=str(uuid.uuid4()),
                order_id=order.id,
                product_id=product.id,
                item_name=product.item_name,
                unit_price=product.price,
                quantity=quantity,
            )
        )

    order.total_amount = total
    db.commit()
    db.refresh(order)
    return order


def get_order(db: Session, order_id: str) -> KioskOrder:
    order = db.get(KioskOrder, order_id)
    if order is None:
        raise NotFoundError(f"주문을 찾을 수 없습니다: {order_id}")
    return order


def pay_order(db: Session, order_id: str) -> KioskOrder:
    """실제 PG 연동 없이 결제를 모의 처리한다."""
    order = get_order(db, order_id)
    if order.status != OrderStatus.CART:
        raise InvalidStateError(f"결제할 수 없는 상태입니다 (현재 상태: {order.status})")
    order.status = OrderStatus.PAID
    order.paid_at = _now()
    db.commit()
    db.refresh(order)
    return order


def dispense_order(db: Session, order_id: str) -> KioskOrder:
    order = get_order(db, order_id)
    if order.status != OrderStatus.PAID:
        raise InvalidStateError(f"수령할 수 없는 상태입니다 (현재 상태: {order.status})")
    order.status = OrderStatus.DISPENSED
    order.dispensed_at = _now()
    db.commit()
    db.refresh(order)
    return order
