"""상담 요청 -> 약사 승인 -> QR 로그인 -> 수령 확정까지의 비즈니스 로직.

HTTP(FastAPI 라우터)와 분리해서, DB 세션만 있으면 단독으로 테스트할 수 있게 만든다.
사용자/약사 인증은 아직 별도 시스템이 없어서 user_id/pharmacist_id를 신뢰된 값으로 그대로 받는다
(실제 인증이 붙으면 그 값으로 교체할 지점).
"""

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from src.consult.models import ApprovedPurchase, ConsultationRequest, ConsultationStatus, PurchaseStatus, VendingMachine

PURCHASE_VALID_MINUTES = 60
QR_TOKEN_VALID_SECONDS = 60


def _now() -> datetime:
    # models.py와 동일하게 naive UTC로 통일 (SQLite가 timezone-aware datetime을 못 지켜서
    # DB에서 읽어온 값과 비교할 때 naive/aware가 섞이면 오류가 난다).
    return datetime.now(timezone.utc).replace(tzinfo=None)


class NotFoundError(Exception):
    pass


class InvalidStateError(Exception):
    pass


def create_consultation(
    db: Session,
    user_id: str,
    chat_summary: str,
    requested_drug_item_seq: str | None = None,
    requested_drug_name: str | None = None,
) -> ConsultationRequest:
    consultation = ConsultationRequest(
        user_id=user_id,
        chat_summary=chat_summary,
        requested_drug_item_seq=requested_drug_item_seq,
        requested_drug_name=requested_drug_name,
    )
    db.add(consultation)
    db.commit()
    db.refresh(consultation)
    return consultation


def list_consultations(db: Session, status: str | None = None) -> list[ConsultationRequest]:
    query = db.query(ConsultationRequest)
    if status:
        query = query.filter(ConsultationRequest.status == status)
    return query.order_by(ConsultationRequest.created_at.desc()).all()


def decide_consultation(
    db: Session,
    consultation_id: str,
    pharmacist_id: str,
    approve: bool,
    reason: str | None = None,
    drug_item_seq: str | None = None,
    drug_item_name: str | None = None,
) -> ConsultationRequest:
    consultation = db.get(ConsultationRequest, consultation_id)
    if consultation is None:
        raise NotFoundError(f"상담 요청을 찾을 수 없습니다: {consultation_id}")
    if consultation.status != ConsultationStatus.PENDING:
        raise InvalidStateError(f"이미 처리된 상담 요청입니다 (현재 상태: {consultation.status})")

    consultation.pharmacist_id = pharmacist_id
    consultation.decision_reason = reason
    consultation.decided_at = _now()

    if approve:
        item_seq = drug_item_seq or consultation.requested_drug_item_seq
        item_name = drug_item_name or consultation.requested_drug_name
        if not item_seq or not item_name:
            raise InvalidStateError("승인하려면 약품 정보(item_seq, item_name)가 필요합니다.")

        consultation.status = ConsultationStatus.APPROVED
        purchase = ApprovedPurchase(
            user_id=consultation.user_id,
            consultation_id=consultation.id,
            drug_item_seq=item_seq,
            drug_item_name=item_name,
            approved_by=pharmacist_id,
            expires_at=_now() + timedelta(minutes=PURCHASE_VALID_MINUTES),
        )
        db.add(purchase)
    else:
        consultation.status = ConsultationStatus.REJECTED

    db.commit()
    db.refresh(consultation)
    return consultation


def rotate_qr_token(db: Session, machine_id: str, machine_name: str | None = None) -> VendingMachine:
    machine = db.get(VendingMachine, machine_id)
    if machine is None:
        machine = VendingMachine(id=machine_id, name=machine_name or machine_id)
        db.add(machine)

    machine.qr_token = secrets.token_urlsafe(16)
    machine.qr_token_expires_at = _now() + timedelta(seconds=QR_TOKEN_VALID_SECONDS)
    db.commit()
    db.refresh(machine)
    return machine


def _expire_if_needed(db: Session, purchase: ApprovedPurchase) -> ApprovedPurchase:
    if purchase.status == PurchaseStatus.PENDING and purchase.expires_at < _now():
        purchase.status = PurchaseStatus.EXPIRED
        db.commit()
        db.refresh(purchase)
    return purchase


def scan_qr(db: Session, machine_id: str, qr_token: str, user_id: str) -> ApprovedPurchase | None:
    """QR 로그인. 토큰이 유효하면 이 사용자의 대기 중인 승인 건을 찾아 반환한다 (없으면 None)."""
    machine = db.get(VendingMachine, machine_id)
    if machine is None or machine.qr_token != qr_token:
        raise InvalidStateError("유효하지 않은 QR입니다.")
    if machine.qr_token_expires_at < _now():
        raise InvalidStateError("만료된 QR입니다. 자판기 화면을 다시 스캔해주세요.")

    candidates = (
        db.query(ApprovedPurchase)
        .filter(ApprovedPurchase.user_id == user_id, ApprovedPurchase.status == PurchaseStatus.PENDING)
        .order_by(ApprovedPurchase.created_at.desc())
        .all()
    )
    for purchase in candidates:
        purchase = _expire_if_needed(db, purchase)
        if purchase.status == PurchaseStatus.PENDING:
            return purchase
    return None


def dispense(db: Session, purchase_id: str, machine_id: str) -> ApprovedPurchase:
    purchase = db.get(ApprovedPurchase, purchase_id)
    if purchase is None:
        raise NotFoundError(f"승인 건을 찾을 수 없습니다: {purchase_id}")

    purchase = _expire_if_needed(db, purchase)
    if purchase.status != PurchaseStatus.PENDING:
        raise InvalidStateError(f"수령할 수 없는 상태입니다 (현재 상태: {purchase.status})")

    purchase.status = PurchaseStatus.DISPENSED
    purchase.dispensed_machine_id = machine_id
    purchase.dispensed_at = _now()
    db.commit()
    db.refresh(purchase)
    return purchase