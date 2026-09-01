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


def test_create_consultation_stores_room_url(db):
    consultation = service.create_consultation(db, user_id="user1", chat_summary="두통 상담")
    assert consultation.room_url == "https://moyak-team.daily.co/test-room"


def test_create_consultation_survives_room_creation_failure(db, monkeypatch):
    monkeypatch.setattr(service.video, "create_room", lambda: None)
    consultation = service.create_consultation(db, user_id="user1", chat_summary="두통 상담")
    assert consultation.status == ConsultationStatus.PENDING
    assert consultation.room_url is None


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


def test_dispense_expired_purchase_raises(db):
    consultation = approved_consultation(db)
    purchase = db.get(ApprovedPurchase, consultation.purchase.id)
    purchase.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
    db.commit()

    with pytest.raises(service.InvalidStateError):
        service.dispense(db, purchase_id=purchase.id, machine_id="M1")