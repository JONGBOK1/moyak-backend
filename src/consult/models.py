"""약사 상담 -> 승인 -> 자판기 수령 흐름의 데이터 모델.

ConsultationRequest: 챗봇 상담 후 사용자가 요청한 약사 상담 건
ConsultationMessage: 화상 상담 중 사용자-약사 채팅 메시지
ApprovedPurchase: 약사가 승인한 구매 건 (유효시간 있음, QR 로그인 시 조회 대상)
VendingMachine: 자판기 1대 = QR 토큰을 발급/보유하는 주체
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String
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
    CANCELLED = "cancelled"  # 사용자가 대기 중 직접 취소


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
    # 화상 상담 종료 시 생성하는 대화 요약 (챗봇 대화 + 상담 중 채팅 기준, 음성 내용은 포함 안 됨)
    summary = Column(String, nullable=True)
    ended_at = Column(DateTime, nullable=True)

    purchase = relationship("ApprovedPurchase", back_populates="consultation", uselist=False)
    messages = relationship(
        "ConsultationMessage", back_populates="consultation", order_by="ConsultationMessage.created_at"
    )


class MessageSender:
    USER = "user"
    PHARMACIST = "pharmacist"


class ConsultationMessage(Base):
    """화상 상담 중 사용자-약사 간 텍스트 채팅 한 건. 상담 종료 시 요약의 재료가 된다."""

    __tablename__ = "consultation_messages"

    id = Column(String, primary_key=True, default=_uuid)
    consultation_id = Column(String, ForeignKey("consultation_requests.id"), nullable=False, index=True)
    sender_role = Column(String, nullable=False)  # MessageSender.USER / PHARMACIST
    sender_id = Column(String, nullable=False)
    content = Column(String, nullable=False)
    created_at = Column(DateTime, default=_now, nullable=False)

    consultation = relationship("ConsultationRequest", back_populates="messages")


class ApprovedPurchase(Base):
    __tablename__ = "approved_purchases"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, nullable=False, index=True)
    consultation_id = Column(String, ForeignKey("consultation_requests.id"), nullable=False)
    drug_item_seq = Column(String, nullable=False)
    drug_item_name = Column(String, nullable=False)
    approved_by = Column(String, nullable=False)  # pharmacist_id
    status = Column(String, default=PurchaseStatus.PENDING, nullable=False)
    # 약사가 승인 시 안내하는 판매가(원). 가격이 있으면 키오스크에서 결제(paid_at)해야 수령 가능.
    # 가격 도입 전 승인 건은 NULL — 결제 없이 수령 가능(기존 동작 유지).
    price = Column(Integer, nullable=True)
    paid_at = Column(DateTime, nullable=True)
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
    # 현재 QR 사이클에서 앱이 스캔해 로그인한 사용자. 승인된 구매 건 유무와 무관하게,
    # 유효한 QR을 스캔하기만 하면 항상 채워진다 (로그인 자체는 항상 성공).
    # 자판기 화면이 이 값을 폴링해서 "누군가 로그인했다"를 감지하고 대기 -> 환영 화면으로 전환한다.
    paired_user_id = Column(String, nullable=True)
    # 로그인한 사용자에게 마침 승인된 구매 건이 있으면 채워진다 (없을 수도 있음).
    paired_purchase_id = Column(String, ForeignKey("approved_purchases.id"), nullable=True)
    # 지도(/api/v1/map) 표시용 위치 정보. 좌표가 없는 자판기는 지도에 나오지 않는다.
    address = Column(String, nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    operating_hours = Column(String, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)


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