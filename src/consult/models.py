"""약사 상담 -> 승인 -> 자판기 수령 흐름의 데이터 모델.

ConsultationRequest: 챗봇 상담 후 사용자가 요청한 약사 상담 건
ApprovedPurchase: 약사가 승인한 구매 건 (유효시간 있음, QR 로그인 시 조회 대상)
VendingMachine: 자판기 1대 = QR 토큰을 발급/보유하는 주체
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from src.consult.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    # SQLite는 timezone-aware datetime을 저장/복원하지 못해 naive로 돌아오므로,
    # 처음부터 naive UTC로 통일해서 비교 시 오류가 나지 않게 한다.
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ConsultationStatus:
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class PurchaseStatus:
    PENDING = "pending"  # 승인됨, 아직 자판기에서 수령 전
    DISPENSED = "dispensed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class OrderStatus:
    CART = "cart"  # 아직 결제 전 (재고는 이미 차감됨)
    PAID = "paid"
    DISPENSED = "dispensed"
    CANCELLED = "cancelled"


class ConsultationRequest(Base):
    __tablename__ = "consultation_requests"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, nullable=False, index=True)
    chat_summary = Column(String, nullable=False)  # 챗봇 대화 요약 (약사가 참고)
    room_url = Column(String, nullable=True)  # 화상 상담방 URL (Daily.co). 생성 실패 시 None
    requested_drug_item_seq = Column(String, nullable=True)
    requested_drug_name = Column(String, nullable=True)
    status = Column(String, default=ConsultationStatus.PENDING, nullable=False)
    pharmacist_id = Column(String, nullable=True)
    decision_reason = Column(String, nullable=True)
    created_at = Column(DateTime, default=_now, nullable=False)
    decided_at = Column(DateTime, nullable=True)

    purchase = relationship("ApprovedPurchase", back_populates="consultation", uselist=False)


class ApprovedPurchase(Base):
    __tablename__ = "approved_purchases"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, nullable=False, index=True)
    consultation_id = Column(String, ForeignKey("consultation_requests.id"), nullable=False)
    drug_item_seq = Column(String, nullable=False)
    drug_item_name = Column(String, nullable=False)
    approved_by = Column(String, nullable=False)  # pharmacist_id
    status = Column(String, default=PurchaseStatus.PENDING, nullable=False)
    created_at = Column(DateTime, default=_now, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    dispensed_machine_id = Column(String, nullable=True)
    dispensed_at = Column(DateTime, nullable=True)

    consultation = relationship("ConsultationRequest", back_populates="purchase")


class VendingMachine(Base):
    __tablename__ = "vending_machines"

    id = Column(String, primary_key=True)  # 자판기 고유 코드
    name = Column(String, nullable=False)
    qr_token = Column(String, nullable=True)
    qr_token_expires_at = Column(DateTime, nullable=True)
    # 현재 QR 사이클에서 앱이 스캔해 로그인/매칭된 승인 건. 자판기 화면이 이 값을 폴링해서
    # "누군가 로그인했다"를 감지하고 대기 화면 -> 환영/수령 화면으로 전환한다.
    paired_purchase_id = Column(String, ForeignKey("approved_purchases.id"), nullable=True)


class Product(Base):
    """자판기별 의약외품 재고. 본인인증 없이 즉시 구매 가능한 품목만 다룬다."""

    __tablename__ = "products"

    id = Column(String, primary_key=True, default=_uuid)
    machine_id = Column(String, ForeignKey("vending_machines.id"), nullable=False, index=True)
    item_seq = Column(String, nullable=False)
    item_name = Column(String, nullable=False)
    company = Column(String, nullable=False)
    category = Column(String, nullable=False)
    badge = Column(String, nullable=True)  # "상처 보호" 같은 짧은 태그
    price = Column(Integer, nullable=False)
    stock = Column(Integer, nullable=False, default=0)


class KioskOrder(Base):
    """의약외품 즉시구매 주문. 본인인증이 필요 없어 사용자 계정과 연결되지 않는다."""

    __tablename__ = "kiosk_orders"

    id = Column(String, primary_key=True, default=_uuid)
    machine_id = Column(String, ForeignKey("vending_machines.id"), nullable=False, index=True)
    status = Column(String, default=OrderStatus.CART, nullable=False)
    total_amount = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=_now, nullable=False)
    paid_at = Column(DateTime, nullable=True)
    dispensed_at = Column(DateTime, nullable=True)

    items = relationship("KioskOrderItem", back_populates="order")


class KioskOrderItem(Base):
    __tablename__ = "kiosk_order_items"

    id = Column(String, primary_key=True, default=_uuid)
    order_id = Column(String, ForeignKey("kiosk_orders.id"), nullable=False)
    product_id = Column(String, ForeignKey("products.id"), nullable=False)
    item_name = Column(String, nullable=False)
    unit_price = Column(Integer, nullable=False)
    quantity = Column(Integer, nullable=False)

    order = relationship("KioskOrder", back_populates="items")