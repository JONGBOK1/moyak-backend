"""약사 상담 -> 승인 -> 자판기 수령 흐름의 데이터 모델.

ConsultationRequest: 챗봇 상담 후 사용자가 요청한 약사 상담 건
ApprovedPurchase: 약사가 승인한 구매 건 (유효시간 있음, QR 로그인 시 조회 대상)
VendingMachine: 자판기 1대 = QR 토큰을 발급/보유하는 주체
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, JSON, LargeBinary, UniqueConstraint
from sqlalchemy.orm import relationship

from src.consult.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    # SQLite는 timezone-aware datetime을 저장/복원하지 못해 naive로 돌아오므로,
    # 처음부터 naive UTC로 통일해서 비교 시 오류가 나지 않게 한다.
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ConsultationStatus:
    CANCELLED = "cancelled"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class OrderStatus:
    CART = "cart"
    PAID = "paid"
    DISPENSED = "dispensed"


class PurchaseStatus:
    PENDING = "pending"  # 승인됨, 아직 자판기에서 수령 전
    DISPENSED = "dispensed"
    EXPIRED = "expired"
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
    price = Column(Integer, nullable=True)
    paid_at = Column(DateTime, nullable=True)
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
    address = Column(String, nullable=False, default="")
    operating_hours = Column(String, nullable=True)
    operating_hours = Column(String, nullable=True)
    latitude = Column(Float, nullable=True, index=True)
    longitude = Column(Float, nullable=True, index=True)
    is_active = Column(Boolean, nullable=False, default=True)
    qr_token = Column(String, nullable=True)
    qr_token_expires_at = Column(DateTime, nullable=True)

    paired_user_id = Column(String, nullable=True)
    paired_purchase_id = Column(String, ForeignKey("approved_purchases.id"), nullable=True)

    inventory_items = relationship("VendingInventory", back_populates="machine", cascade="all, delete-orphan")


Index("ix_vending_machines_latitude_longitude", VendingMachine.latitude, VendingMachine.longitude)


class VendingInventory(Base):
    __tablename__ = "vending_inventory"

    id = Column(String, primary_key=True, default=_uuid)
    machine_id = Column(String, ForeignKey("vending_machines.id"), nullable=False, index=True)
    drug_id = Column(String, nullable=False, index=True)
    drug_name = Column(String, nullable=False)
    category = Column(String, nullable=False, default="기타")
    price = Column(Integer, nullable=False, default=0)
    stock_count = Column(Integer, nullable=False, default=0)

    machine = relationship("VendingMachine", back_populates="inventory_items")


Index("ux_vending_inventory_machine_drug", VendingInventory.machine_id, VendingInventory.drug_id, unique=True)


class ConsultationSession(Base):
    __tablename__ = "consultation_sessions"
    consultation_id = Column(String, ForeignKey("consultation_requests.id"), primary_key=True)
    pharmacist_id = Column(String, nullable=False)
    user_consent_at = Column(DateTime)
    pharmacist_consent_at = Column(DateTime)
    ended_at = Column(DateTime)
    revision = Column(Integer, nullable=False, default=0)


class ConsultationMessage(Base):
    __tablename__ = "consultation_messages"
    id = Column(Integer, primary_key=True, autoincrement=True)
    consultation_id = Column(String, ForeignKey("consultation_requests.id"), nullable=False, index=True)
    sender_id = Column(String, nullable=False)
    sender_role = Column(String, nullable=False)
    client_id = Column(String, nullable=False)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=_now, nullable=False)
    __table_args__ = (UniqueConstraint("consultation_id", "sender_role", "sender_id", "client_id"),)


class ConsultationAudio(Base):
    __tablename__ = "consultation_audio"
    id = Column(Integer, primary_key=True, autoincrement=True)
    consultation_id = Column(String, ForeignKey("consultation_requests.id"), nullable=False, index=True)
    sender_id = Column(String, nullable=False)
    sender_role = Column(String, nullable=False)
    client_id = Column(String, nullable=False)
    start_ms = Column(Integer, nullable=False)
    filename = Column(String, nullable=False)
    audio = Column(LargeBinary)
    digest = Column(String, nullable=False)
    transcript = Column(Text)
    status = Column(String, nullable=False, default="queued", index=True)
    lease = Column(String)
    started_at = Column(DateTime)
    created_at = Column(DateTime, default=_now, nullable=False)
    error = Column(String)
    __table_args__ = (UniqueConstraint("consultation_id", "sender_role", "sender_id", "client_id"),)


class ConsultationSummary(Base):
    __tablename__ = "consultation_summaries"
    consultation_id = Column(String, ForeignKey("consultation_requests.id"), primary_key=True)
    status = Column(String, nullable=False, default="queued", index=True)
    source = Column(JSON, nullable=False)
    draft = Column(JSON)
    published = Column(JSON)
    lease = Column(String)
    started_at = Column(DateTime)
    error = Column(String)
    reviewed_by = Column(String)
    reviewed_at = Column(DateTime)


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
