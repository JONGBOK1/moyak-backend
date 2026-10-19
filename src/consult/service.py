"""상담 요청 -> 약사 승인 -> QR 로그인 -> 수령 확정까지의 비즈니스 로직.

HTTP(FastAPI 라우터)와 분리해서, DB 세션만 있으면 단독으로 테스트할 수 있게 만든다.
사용자/약사 인증은 아직 별도 시스템이 없어서 user_id/pharmacist_id를 신뢰된 값으로 그대로 받는다
(실제 인증이 붙으면 그 값으로 교체할 지점).
"""

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from src.consult import summary as summary_module
from src.consult.db import is_shared_db
from src.consult import video
from src.consult.models import (
    ApprovedPurchase,
    ConsultationMessage,
    ConsultationRequest,
    ConsultationSession,
    ConsultationStatus,
    MessageSender,
    PurchaseStatus,
    VendingMachine,
)

MAX_MESSAGE_LENGTH = 1000

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
    machine_code: str | None = None,
) -> ConsultationRequest:
    machine_code = (machine_code or "").strip() or None
    if machine_code is not None:
        machine = db.get(VendingMachine, machine_code)
        if machine is None or not machine.is_active:
            raise NotFoundError(f"선택한 키오스크를 찾을 수 없습니다: {machine_code}")
    consultation = ConsultationRequest(
        machine_code=machine_code,
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
    db: Session, status: str | None = None, user_id: str | None = None, pharmacist_id: str | None = None
) -> list[ConsultationRequest]:
    query = db.query(ConsultationRequest)
    if status:
        query = query.filter(ConsultationRequest.status == status)
    if user_id:
        query = query.filter(ConsultationRequest.user_id == user_id)
    if pharmacist_id:
        query = query.filter(ConsultationRequest.pharmacist_id == pharmacist_id)
    return query.order_by(ConsultationRequest.created_at.desc()).all()


def get_consultation(db: Session, consultation_id: str) -> ConsultationRequest:
    """채팅 화면이 자신이 요청한 상담의 현재 상태(대기/승인/거절)를 폴링할 때 쓴다."""
    consultation = db.get(ConsultationRequest, consultation_id)
    if consultation is None:
        raise NotFoundError(f"상담 요청을 찾을 수 없습니다: {consultation_id}")
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
        if price is not None and price < 0:
            raise InvalidStateError("가격은 0원 이상이어야 합니다.")
        if consultation.machine_code:
            # 사용자가 고른 키오스크에서 바로 받을 수 있는 약만 승인 (재고 정보가 있는 DB에서만)
            from src.consult.inventory import machine_medicines

            stock = machine_medicines(db, consultation.machine_code)
            if stock is not None:
                item = next((m for m in stock if m["item_seq"] == str(item_seq)), None)
                if item is None or item["stock"] < 1:
                    raise InvalidStateError(
                        f"환자가 선택한 키오스크({consultation.machine_code})에 재고가 없는 약입니다. 키오스크 재고 목록에서 골라주세요.")
                if price is None:
                    price = item["price"]  # 가격을 따로 안 넣으면 키오스크 판매가

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


def cancel_consultation(db: Session, consultation_id: str) -> ConsultationRequest:
    """대기 중인 상담 요청을 사용자가 직접 취소할 때 쓴다."""
    consultation = db.get(ConsultationRequest, consultation_id)
    if consultation is None:
        raise NotFoundError(f"상담 요청을 찾을 수 없습니다: {consultation_id}")
    if consultation.status != ConsultationStatus.PENDING:
        raise InvalidStateError(f"대기 중인 상담만 취소할 수 있습니다 (현재 상태: {consultation.status})")

    consultation.status = ConsultationStatus.CANCELLED
    consultation.decided_at = _now()
    db.commit()
    db.refresh(consultation)
    return consultation


def add_message(
    db: Session, consultation_id: str, sender_role: str, sender_id: str, content: str,
    client_id: str | None = None,
) -> ConsultationMessage:
    """화상 상담 중 채팅 한 건 저장. 상담이 종료(요약 생성)된 뒤에는 더 보낼 수 없다."""
    consultation = get_consultation(db, consultation_id)
    if sender_role not in (MessageSender.USER, MessageSender.PHARMACIST):
        raise InvalidStateError(f"알 수 없는 발신자 유형입니다: {sender_role}")
    if sender_role == MessageSender.USER and sender_id != consultation.user_id:
        raise InvalidStateError("본인의 상담에만 메시지를 보낼 수 있습니다.")
    content = content.strip()
    key = client_id or str(uuid.uuid4())
    def existing_message():
        return db.query(ConsultationMessage).filter_by(consultation_id=consultation_id,
            sender_role=sender_role, sender_id=sender_id, client_id=key).first()
    def check_existing(message):
        if message.content != content:
            raise InvalidStateError('같은 client_id로 다른 메시지를 보낼 수 없습니다.')
        return message
    existing = existing_message()
    if existing:
        return check_existing(existing)
    if consultation.ended_at is not None:
        raise InvalidStateError("이미 종료된 상담입니다.")
    room = db.get(ConsultationSession, consultation_id)
    if room is not None and room.ended_at is not None:  # 약사가 상담을 종료(요약 검토 중)한 뒤엔 채팅 불가
        raise InvalidStateError("이미 종료된 상담입니다.")
    content = content.strip()
    if not content:
        raise InvalidStateError("메시지 내용이 비어 있습니다.")
    if len(content) > MAX_MESSAGE_LENGTH:
        raise InvalidStateError(f"메시지는 {MAX_MESSAGE_LENGTH}자 이하로 보내주세요.")

    message = ConsultationMessage(
        consultation_id=consultation.id, sender_role=sender_role, sender_id=sender_id, content=content, client_id=key
    )
    db.add(message)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = existing_message()
        if existing:
            return check_existing(existing)
        raise
    db.refresh(message)
    return message


def list_messages(db: Session, consultation_id: str) -> list[ConsultationMessage]:
    return list(get_consultation(db, consultation_id).messages)


def end_consultation(db: Session, consultation_id: str, llm) -> ConsultationRequest:
    """화상 상담 종료 — 대화 요약을 만들어 저장한다. 이미 종료됐으면 기존 요약을 그대로 돌려준다
    (사용자와 약사가 둘 다 종료를 눌러도 요약은 한 번만 생성)."""
    consultation = get_consultation(db, consultation_id)
    if consultation.ended_at is not None:
        return consultation
    if consultation.status == ConsultationStatus.CANCELLED:
        raise InvalidStateError("취소된 상담입니다.")
    if db.get(ConsultationSession, consultation_id) is not None:
        # 약사가 연결한 상담은 '약사 검토 후 공개' 흐름을 쓴다 — 검토 없는 즉시 요약 공개를 막는다
        raise InvalidStateError("약사가 연결한 상담은 약사가 종료하고 요약을 검토한 뒤 공개합니다.")

    consultation.summary = summary_module.summarize(llm, consultation, DIRECT_REQUEST_SUMMARY)
    consultation.ended_at = _now()
    db.commit()
    db.refresh(consultation)
    return consultation


def rotate_qr_token(db: Session, machine_id: str, machine_name: str | None = None) -> VendingMachine:
    machine = db.get(VendingMachine, machine_id)
    if machine is None:
        if is_shared_db(db):  # 팀 DB에는 등록된 자판기만 — 주소를 잘못 쳐도 자판기가 새로 생기지 않게
            raise NotFoundError(f"등록되지 않은 자판기입니다: {machine_id}")
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

    now = _now()
    # Claim first, then update stock and history in the SAME transaction.
    changed = db.execute(update(ApprovedPurchase).where(
        ApprovedPurchase.id == purchase_id, ApprovedPurchase.status == PurchaseStatus.PENDING,
        ApprovedPurchase.expires_at > now,
    ).values(status=PurchaseStatus.DISPENSED, dispensed_machine_id=machine_id, dispensed_at=now)).rowcount
    if not changed:
        db.rollback()
        raise InvalidStateError('이미 수령했거나 만료된 승인 건입니다.')
    if is_shared_db(db):
        from src.consult.inventory import record_dispense, InventoryError
        try:
            record_dispense(db, purchase, machine_id, now)
        except InventoryError as error:
            db.rollback()
            raise InvalidStateError(str(error)) from error
        except Exception:
            db.rollback()
            raise

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
