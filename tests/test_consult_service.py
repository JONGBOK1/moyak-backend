from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.consult import service
from src.consult.db import Base
from src.consult.models import ApprovedPurchase, ConsultationStatus, PurchaseStatus


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def no_real_daily_calls(monkeypatch):
    # create_consultation()이 실제 Daily.co API를 호출하지 않도록 막는다
    # (테스트가 느려지고 무료 할당량을 깎는 걸 방지).
    monkeypatch.setattr(service.video, "create_room", lambda: "https://moyak-team.daily.co/test-room")


def approved_consultation(db, user_id="user1"):
    consultation = service.create_consultation(
        db, user_id=user_id, chat_summary="두통 상담", requested_drug_item_seq="A1", requested_drug_name="약A"
    )
    return service.decide_consultation(db, consultation_id=consultation.id, pharmacist_id="pharm1", approve=True)


def test_create_consultation_defaults_to_pending(db):
    consultation = service.create_consultation(db, user_id="user1", chat_summary="두통 상담")
    assert consultation.status == ConsultationStatus.PENDING
    assert consultation.id


def test_get_consultation_returns_it(db):
    created = service.create_consultation(db, user_id="user1", chat_summary="두통 상담")
    fetched = service.get_consultation(db, created.id)
    assert fetched.id == created.id


def test_get_consultation_not_found_raises(db):
    with pytest.raises(service.NotFoundError):
        service.get_consultation(db, "nope")


def test_list_consultations_filters_by_user_id(db):
    service.create_consultation(db, user_id="user1", chat_summary="a")
    service.create_consultation(db, user_id="user2", chat_summary="b")

    results = service.list_consultations(db, user_id="user1")
    assert len(results) == 1
    assert results[0].user_id == "user1"


def test_list_consultations_filters_by_pharmacist_id(db):
    approved_consultation(db, user_id="user1")  # pharm1이 처리
    consultation2 = service.create_consultation(db, user_id="user2", chat_summary="b", requested_drug_item_seq="A2", requested_drug_name="약B")
    service.decide_consultation(db, consultation_id=consultation2.id, pharmacist_id="pharm2", approve=True)

    results = service.list_consultations(db, pharmacist_id="pharm1")
    assert len(results) == 1
    assert results[0].pharmacist_id == "pharm1"


def test_list_consultations_by_pharmacist_id_excludes_pending(db):
    # 아직 아무도 처리하지 않은 상담은 pharmacist_id가 없어서 필터에 걸리지 않는다.
    service.create_consultation(db, user_id="user1", chat_summary="a")

    results = service.list_consultations(db, pharmacist_id="pharm1")
    assert results == []


def test_create_consultation_stores_room_url(db):
    consultation = service.create_consultation(db, user_id="user1", chat_summary="두통 상담")
    assert consultation.room_url == "https://moyak-team.daily.co/test-room"


def test_create_consultation_survives_room_creation_failure(db, monkeypatch):
    monkeypatch.setattr(service.video, "create_room", lambda: None)
    consultation = service.create_consultation(db, user_id="user1", chat_summary="두통 상담")
    assert consultation.status == ConsultationStatus.PENDING
    assert consultation.room_url is None


def test_create_consultation_without_chat_summary_uses_default_text(db):
    # 챗봇 없이 네비게이션바 등에서 바로 상담을 시작한 경우
    consultation = service.create_consultation(db, user_id="user1")
    assert consultation.chat_summary == service.DIRECT_REQUEST_SUMMARY
    assert consultation.status == ConsultationStatus.PENDING


def test_decide_consultation_approve_creates_purchase(db):
    consultation = approved_consultation(db)
    assert consultation.status == ConsultationStatus.APPROVED
    assert consultation.purchase is not None
    assert consultation.purchase.status == PurchaseStatus.PENDING
    assert consultation.purchase.drug_item_name == "약A"


def test_decide_consultation_reject_creates_no_purchase(db):
    consultation = service.create_consultation(db, user_id="user1", chat_summary="두통 상담")
    decided = service.decide_consultation(db, consultation_id=consultation.id, pharmacist_id="pharm1", approve=False, reason="증거 부족")
    assert decided.status == ConsultationStatus.REJECTED
    assert decided.purchase is None


def test_cancel_consultation_marks_cancelled(db):
    consultation = service.create_consultation(db, user_id="user1", chat_summary="두통 상담")
    cancelled = service.cancel_consultation(db, consultation_id=consultation.id)
    assert cancelled.status == ConsultationStatus.CANCELLED
    assert cancelled.decided_at is not None


def test_cancel_consultation_already_decided_raises(db):
    consultation = approved_consultation(db)
    with pytest.raises(service.InvalidStateError):
        service.cancel_consultation(db, consultation_id=consultation.id)


def test_cancel_consultation_not_found_raises(db):
    with pytest.raises(service.NotFoundError):
        service.cancel_consultation(db, consultation_id="nope")


def test_decide_consultation_twice_raises(db):
    consultation = service.create_consultation(db, user_id="user1", chat_summary="두통 상담", requested_drug_item_seq="A1", requested_drug_name="약A")
    service.decide_consultation(db, consultation_id=consultation.id, pharmacist_id="pharm1", approve=True)
    with pytest.raises(service.InvalidStateError):
        service.decide_consultation(db, consultation_id=consultation.id, pharmacist_id="pharm1", approve=True)


def test_decide_consultation_approve_without_drug_info_raises(db):
    consultation = service.create_consultation(db, user_id="user1", chat_summary="두통 상담")
    with pytest.raises(service.InvalidStateError):
        service.decide_consultation(db, consultation_id=consultation.id, pharmacist_id="pharm1", approve=True)


def test_decide_consultation_not_found_raises(db):
    with pytest.raises(service.NotFoundError):
        service.decide_consultation(db, consultation_id="nope", pharmacist_id="pharm1", approve=True)


def test_scan_qr_wrong_token_raises(db):
    service.rotate_qr_token(db, machine_id="M1")
    with pytest.raises(service.InvalidStateError):
        service.scan_qr(db, machine_id="M1", qr_token="wrong-token", user_id="user1")


def test_scan_qr_unknown_machine_raises(db):
    with pytest.raises(service.InvalidStateError):
        service.scan_qr(db, machine_id="ghost", qr_token="anything", user_id="user1")


def test_scan_qr_finds_pending_purchase_for_correct_user(db):
    consultation = approved_consultation(db, user_id="user1")
    machine = service.rotate_qr_token(db, machine_id="M1")

    found = service.scan_qr(db, machine_id="M1", qr_token=machine.qr_token, user_id="user1")
    assert found is not None
    assert found.id == consultation.purchase.id


def test_scan_qr_returns_none_for_other_user(db):
    approved_consultation(db, user_id="user1")
    machine = service.rotate_qr_token(db, machine_id="M1")

    found = service.scan_qr(db, machine_id="M1", qr_token=machine.qr_token, user_id="user2")
    assert found is None


def test_scan_qr_expired_token_raises(db):
    machine = service.rotate_qr_token(db, machine_id="M1")
    machine.qr_token_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
    db.commit()

    with pytest.raises(service.InvalidStateError):
        service.scan_qr(db, machine_id="M1", qr_token=machine.qr_token, user_id="user1")


def test_dispense_marks_dispensed(db):
    consultation = approved_consultation(db)
    purchase_id = consultation.purchase.id

    dispensed = service.dispense(db, purchase_id=purchase_id, machine_id="M1")
    assert dispensed.status == PurchaseStatus.DISPENSED
    assert dispensed.dispensed_machine_id == "M1"
    assert dispensed.dispensed_at is not None


def test_dispense_twice_raises(db):
    consultation = approved_consultation(db)
    purchase_id = consultation.purchase.id
    service.dispense(db, purchase_id=purchase_id, machine_id="M1")

    with pytest.raises(service.InvalidStateError):
        service.dispense(db, purchase_id=purchase_id, machine_id="M1")


def test_dispense_not_found_raises(db):
    with pytest.raises(service.NotFoundError):
        service.dispense(db, purchase_id="nope", machine_id="M1")


def test_scan_qr_pairs_machine_to_purchase(db):
    consultation = approved_consultation(db, user_id="user1")
    machine = service.rotate_qr_token(db, machine_id="M1")

    service.scan_qr(db, machine_id="M1", qr_token=machine.qr_token, user_id="user1")

    _, user_id, purchase = service.get_machine_session(db, machine_id="M1")
    assert user_id == "user1"
    assert purchase is not None
    assert purchase.id == consultation.purchase.id


def test_get_machine_session_unpaired_by_default(db):
    service.rotate_qr_token(db, machine_id="M1")
    machine, user_id, purchase = service.get_machine_session(db, machine_id="M1")
    assert user_id is None
    assert purchase is None


def test_get_machine_session_not_found_raises(db):
    with pytest.raises(service.NotFoundError):
        service.get_machine_session(db, machine_id="ghost")


def test_scan_qr_logs_in_even_without_pending_purchase(db):
    # QR 로그인 자체는 승인된 구매 건이 없어도 항상 성사되어야 한다.
    machine = service.rotate_qr_token(db, machine_id="M1")
    found = service.scan_qr(db, machine_id="M1", qr_token=machine.qr_token, user_id="user_no_purchase")

    assert found is None
    _, user_id, purchase = service.get_machine_session(db, machine_id="M1")
    assert user_id == "user_no_purchase"
    assert purchase is None


def test_rotate_qr_token_clears_previous_pairing(db):
    consultation = approved_consultation(db, user_id="user1")
    machine = service.rotate_qr_token(db, machine_id="M1")
    service.scan_qr(db, machine_id="M1", qr_token=machine.qr_token, user_id="user1")

    service.rotate_qr_token(db, machine_id="M1")  # 새 QR 발급 = 이전 로그인/페어링 무효화

    _, user_id, purchase = service.get_machine_session(db, machine_id="M1")
    assert user_id is None
    assert purchase is None
    assert consultation.purchase.status == PurchaseStatus.PENDING  # 구매 건 자체는 그대로


def test_dispense_clears_machine_pairing(db):
    consultation = approved_consultation(db, user_id="user1")
    machine = service.rotate_qr_token(db, machine_id="M1")
    service.scan_qr(db, machine_id="M1", qr_token=machine.qr_token, user_id="user1")

    service.dispense(db, purchase_id=consultation.purchase.id, machine_id="M1")

    _, user_id, purchase = service.get_machine_session(db, machine_id="M1")
    assert user_id is None
    assert purchase is None


def test_dispense_expired_purchase_raises(db):
    consultation = approved_consultation(db)
    purchase = db.get(ApprovedPurchase, consultation.purchase.id)
    purchase.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
    db.commit()

    with pytest.raises(service.InvalidStateError):
        service.dispense(db, purchase_id=purchase.id, machine_id="M1")


def test_list_purchases_for_user_returns_all_statuses(db):
    approved_consultation(db, user_id="user1")
    results = service.list_purchases_for_user(db, user_id="user1")
    assert len(results) == 1
    assert results[0].status == PurchaseStatus.PENDING


def test_list_purchases_for_user_marks_expired(db):
    consultation = approved_consultation(db, user_id="user1")
    purchase = db.get(ApprovedPurchase, consultation.purchase.id)
    purchase.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
    db.commit()

    results = service.list_purchases_for_user(db, user_id="user1")
    assert results[0].status == PurchaseStatus.EXPIRED


def test_list_purchases_for_user_excludes_other_users(db):
    approved_consultation(db, user_id="user1")
    results = service.list_purchases_for_user(db, user_id="user2")
    assert results == []

def priced_purchase(db, user_id="user1", price=3500):
    consultation = service.create_consultation(db, user_id=user_id)
    consultation = service.decide_consultation(
        db, consultation_id=consultation.id, pharmacist_id="pharm1", approve=True,
        drug_item_seq="A1", drug_item_name="약A", price=price,
    )
    return consultation.purchase


def logged_in_machine(db, user_id="user1", machine_id="M1"):
    machine = service.rotate_qr_token(db, machine_id=machine_id)
    service.scan_qr(db, machine_id=machine_id, qr_token=machine.qr_token, user_id=user_id)


def test_decide_consultation_stores_price(db):
    assert priced_purchase(db, price=4200).price == 4200


def test_decide_consultation_negative_price_raises(db):
    consultation = service.create_consultation(db, user_id="user1")
    with pytest.raises(service.InvalidStateError):
        service.decide_consultation(
            db, consultation_id=consultation.id, pharmacist_id="pharm1", approve=True,
            drug_item_seq="A1", drug_item_name="약A", price=-1,
        )


def test_dispense_priced_purchase_before_payment_raises(db):
    purchase = priced_purchase(db)
    with pytest.raises(service.InvalidStateError):
        service.dispense(db, purchase_id=purchase.id, machine_id="M1")


def test_pay_purchases_then_dispense_and_logs_out(db):
    p1, p2 = priced_purchase(db, price=3500), priced_purchase(db, price=3000)
    logged_in_machine(db)

    paid = service.pay_purchases(db, machine_id="M1", purchase_ids=[p1.id, p2.id])
    assert all(p.paid_at is not None for p in paid)
    _, user_id, _ = service.get_machine_session(db, machine_id="M1")
    assert user_id is None  # 결제 완료 시 자동 로그아웃

    assert service.dispense(db, purchase_id=p1.id, machine_id="M1").status == PurchaseStatus.DISPENSED
    assert service.dispense(db, purchase_id=p2.id, machine_id="M1").status == PurchaseStatus.DISPENSED


def test_pay_purchases_requires_login(db):
    purchase = priced_purchase(db)
    service.rotate_qr_token(db, machine_id="M1")
    with pytest.raises(service.InvalidStateError):
        service.pay_purchases(db, machine_id="M1", purchase_ids=[purchase.id])


def test_pay_purchases_rejects_other_users_purchase(db):
    others = priced_purchase(db, user_id="user2")
    logged_in_machine(db, user_id="user1")
    with pytest.raises(service.NotFoundError):
        service.pay_purchases(db, machine_id="M1", purchase_ids=[others.id])


def test_pay_purchases_twice_raises(db):
    purchase = priced_purchase(db)
    logged_in_machine(db)
    service.pay_purchases(db, machine_id="M1", purchase_ids=[purchase.id])
    logged_in_machine(db)
    with pytest.raises(service.InvalidStateError):
        service.pay_purchases(db, machine_id="M1", purchase_ids=[purchase.id])
