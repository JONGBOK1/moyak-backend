"""상담 요청 -> 약사 승인 -> QR 로그인 -> 수령 확정까지의 비즈니스 로직.

HTTP(FastAPI 라우터)와 분리해서, DB 세션만 있으면 단독으로 테스트할 수 있게 만든다.
사용자/약사 인증은 아직 별도 시스템이 없어서 user_id/pharmacist_id를 신뢰된 값으로 그대로 받는다
(실제 인증이 붙으면 그 값으로 교체할 지점).
"""

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session
from sqlalchemy import update

from src.consult import video
from src.consult.models import ApprovedPurchase, ConsultationRequest, ConsultationStatus, PurchaseStatus, VendingMachine, ConsultationSession

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


DIRECT_REQUEST_SUMMARY = "직접 상담 요청 (사전 챗봇 대화 없음 — 화상으로 바로 문진 필요)"


def create_consultation(
    db: Session,
    user_id: str,
    chat_summary: str | None = None,
    requested_drug_item_seq: str | None = None,
    requested_drug_name: str | None = None,
) -> ConsultationRequest:
    consultation = ConsultationRequest(
        user_id=user_id,
        chat_summary=chat_summary or DIRECT_REQUEST_SUMMARY,
        room_url=video.create_room(),
        requested_drug_item_seq=requested_drug_item_seq,
        requested_drug_name=requested_drug_name,
    )
    db.add(consultation)
    db.commit()
    db.refresh(consultation)
    return consultation


def list_consultations(
    db: Session, status: str | None = None, user_id: str | None = None
) -> list[ConsultationRequest]:
    query = db.query(ConsultationRequest)
    if status:
        query = query.filter(ConsultationRequest.status == status)
    if user_id:
        query = query.filter(ConsultationRequest.user_id == user_id)
    return query.order_by(ConsultationRequest.created_at.desc()).all()


def get_consultation(db: Session, consultation_id: str) -> ConsultationRequest:
    """채팅 화면이 자신이 요청한 상담의 현재 상태(대기/승인/거절)를 폴링할 때 쓴다."""
    consultation = db.get(ConsultationRequest, consultation_id)
    if consultation is None:
        raise NotFoundError(f"상담 요청을 찾을 수 없습니다: {consultation_id}")
    return consultation


def cancel_consultation(db: Session, consultation_id: str) -> ConsultationRequest:
    consultation = get_consultation(db, consultation_id)
    if db.get(ConsultationSession, consultation_id):
        raise InvalidStateError("이미 약사가 연결된 상담입니다. 상담 종료 기능을 사용해주세요.")
    changed = db.execute(update(ConsultationRequest).where(
        ConsultationRequest.id == consultation_id,
        ConsultationRequest.status == ConsultationStatus.PENDING,
        ConsultationRequest.pharmacist_id.is_(None),
    ).values(status=ConsultationStatus.CANCELLED)).rowcount
    if not changed:
        raise InvalidStateError("대기 중인 상담만 취소할 수 있습니다.")
    db.commit()
    db.refresh(consultation)
    return consultation


def decide_consultation(
    db: Session,
    consultation_id: str,
    pharmacist_id: str,
    approve: bool,
    reason: str | None = None,
    drug_item_seq: str | None = None,
    drug_item_name: str | None = None,
    price: int | None = None,
) -> ConsultationRequest:
    db.execute(update(ConsultationSession).where(ConsultationSession.consultation_id == consultation_id)
               .values(revision=ConsultationSession.revision + 1))
    consultation = db.get(ConsultationRequest, consultation_id)
    if consultation is None:
        raise NotFoundError(f"상담 요청을 찾을 수 없습니다: {consultation_id}")
    if consultation.status != ConsultationStatus.PENDING:
        raise InvalidStateError(f"이미 처리된 상담 요청입니다 (현재 상태: {consultation.status})")
    room = db.get(ConsultationSession, consultation_id)
    if room and (room.pharmacist_id != pharmacist_id or room.ended_at):
        raise InvalidStateError("담당 약사만 진행 중인 상담을 처리할 수 있습니다.")

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
            price=price,
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
    machine.paired_user_id = None  # 새 QR 사이클이 시작되면 이전 로그인은 무효
    machine.paired_purchase_id = None
    db.commit()
    db.refresh(machine)
    return machine


def get_machine_session(db: Session, machine_id: str) -> tuple[VendingMachine, str | None, ApprovedPurchase | None]:
    """자판기 화면이 폴링해서 '누군가 QR을 스캔해 로그인했는지'를 확인할 때 쓴다.

    로그인(paired_user_id)과 승인된 구매 건(paired_purchase_id)은 별개다 —
    로그인은 유효한 QR을 스캔하기만 하면 항상 성사되고, 구매 건은 있을 수도 없을 수도 있다.
    """
    machine = db.get(VendingMachine, machine_id)
    if machine is None:
        raise NotFoundError(f"자판기를 찾을 수 없습니다: {machine_id}")
    if machine.paired_user_id is None:
        return machine, None, None

    if machine.paired_purchase_id is None:
        return machine, machine.paired_user_id, None

    purchase = db.get(ApprovedPurchase, machine.paired_purchase_id)
    if purchase is not None:
        purchase = _expire_if_needed(db, purchase)
    if purchase is None or purchase.status != PurchaseStatus.PENDING:
        # 이미 수령됐거나 만료된 건은 더 이상 유효한 구매 건이 아니다 (로그인 자체는 유지).
        machine.paired_purchase_id = None
        db.commit()
        return machine, machine.paired_user_id, None

    return machine, machine.paired_user_id, purchase


def _expire_if_needed(db: Session, purchase: ApprovedPurchase) -> ApprovedPurchase:
    if purchase.status == PurchaseStatus.PENDING and purchase.expires_at < _now():
        purchase.status = PurchaseStatus.EXPIRED
        db.commit()
        db.refresh(purchase)
    return purchase


def scan_qr(db: Session, machine_id: str, qr_token: str, user_id: str) -> ApprovedPurchase | None:
    """QR 로그인. 토큰이 유효하면 로그인은 항상 성사되고(자판기가 이 사용자로 페어링됨),
    그중 대기 중인 승인 건이 있으면 함께 반환한다 (없으면 None — 로그인 자체는 여전히 성공)."""
    machine = db.get(VendingMachine, machine_id)
    if machine is None or machine.qr_token != qr_token:
        raise InvalidStateError("유효하지 않은 QR입니다.")
    if machine.qr_token_expires_at < _now():
        raise InvalidStateError("만료된 QR입니다. 자판기 화면을 다시 스캔해주세요.")

    machine.paired_user_id = user_id

    candidates = (
        db.query(ApprovedPurchase)
        .filter(ApprovedPurchase.user_id == user_id, ApprovedPurchase.status == PurchaseStatus.PENDING)
        .order_by(ApprovedPurchase.created_at.desc())
        .all()
    )
    for purchase in candidates:
        purchase = _expire_if_needed(db, purchase)
        if purchase.status == PurchaseStatus.PENDING:
            machine.paired_purchase_id = purchase.id
            db.commit()
            return purchase

    machine.paired_purchase_id = None
    db.commit()
    return None


def dispense(db: Session, purchase_id: str, machine_id: str) -> ApprovedPurchase:
    purchase = db.get(ApprovedPurchase, purchase_id)
    if purchase is None:
        raise NotFoundError(f"승인 건을 찾을 수 없습니다: {purchase_id}")

    purchase = _expire_if_needed(db, purchase)
    if purchase.status != PurchaseStatus.PENDING:
        raise InvalidStateError(f"수령할 수 없는 상태입니다 (현재 상태: {purchase.status})")
    if purchase.price is not None and purchase.paid_at is None:
        raise InvalidStateError("결제가 완료되지 않은 승인 건입니다. 결제 후 수령할 수 있습니다.")

    purchase.status = PurchaseStatus.DISPENSED
    purchase.dispensed_machine_id = machine_id
    purchase.dispensed_at = _now()

    machine = db.get(VendingMachine, machine_id)
    if machine is not None and machine.paired_purchase_id == purchase.id:
        machine.paired_purchase_id = None
        machine.paired_user_id = None  # 수령까지 끝났으니 다음 고객을 위해 로그아웃

    db.commit()
    db.refresh(purchase)
    return purchase


def pay_purchases(db: Session, machine_id: str, purchase_ids: list[str]) -> list[ApprovedPurchase]:
    """키오스크에서 승인된 약(여러 건)을 한 번에 결제(모의)한다.

    자판기에 QR 로그인한 본인의 대기 중 승인 건만 결제할 수 있다. 결제가 끝나면
    안내 문구("결제가 완료되면 자동 로그아웃")대로 자판기 로그인을 해제한다 —
    결제된 건은 purchase_id로 수령하므로 로그인이 풀려도 수령에는 지장이 없다.
    """
    if not purchase_ids:
        raise InvalidStateError("결제할 승인 건이 없습니다.")
    machine = db.get(VendingMachine, machine_id)
    if machine is None or machine.paired_user_id is None:
        raise InvalidStateError("자판기에 로그인된 사용자가 없습니다. QR로 다시 로그인해주세요.")

    purchases = []
    for pid in dict.fromkeys(purchase_ids):  # 중복 id 제거, 순서 유지
        purchase = db.get(ApprovedPurchase, pid)
        if purchase is None or purchase.user_id != machine.paired_user_id:
            raise NotFoundError(f"승인 건을 찾을 수 없습니다: {pid}")
        purchase = _expire_if_needed(db, purchase)
        if purchase.status != PurchaseStatus.PENDING:
            raise InvalidStateError(f"결제할 수 없는 상태입니다: {purchase.drug_item_name} (현재 상태: {purchase.status})")
        if purchase.paid_at is not None:
            raise InvalidStateError(f"이미 결제된 승인 건입니다: {purchase.drug_item_name}")
        purchases.append(purchase)

    now = _now()
    for purchase in purchases:
        purchase.paid_at = now
    machine.paired_user_id = None
    machine.paired_purchase_id = None
    db.commit()
    for purchase in purchases:
        db.refresh(purchase)
    return purchases


def list_purchases_for_user(db: Session, user_id: str) -> list[ApprovedPurchase]:
    """마이페이지의 '전자 구매 허가서' 목록에서 쓴다. 상태와 무관하게 전부 반환하고,
    화면에서 만료 여부를 바로 알 수 있게 조회 시점에 만료 처리를 반영한다."""
    purchases = (
        db.query(ApprovedPurchase)
        .filter(ApprovedPurchase.user_id == user_id)
        .order_by(ApprovedPurchase.created_at.desc())
        .all()
    )
    return [_expire_if_needed(db, p) for p in purchases]
