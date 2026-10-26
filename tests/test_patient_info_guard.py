"""약사용 환자 정보(/pharmacist/patients) 접근 규칙 + 안전 판정 '확인 불가' 표시 (2026-10-26, 선우 브랜치 통합 때 보강)."""
import urllib.parse

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.routes import patient_info
from src.consult.auth import Actor
from src.consult.db import Base, get_db
from src.consult.models import ConsultationRequest, ConsultationStatus
from src.consult.patient_safety import Finding, Level, SafetyReport

PATIENT = "00000000-0000-4000-8000-0000000000a1"


def headers(pid, role="pharmacist"):
    return {"X-Moyak-User-Id": urllib.parse.quote(pid), "X-Moyak-Role": role}


@pytest.fixture
def setup():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(patient_info.router)

    def dependency():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = dependency
    with TestClient(app) as client:
        yield client, factory
    engine.dispose()


def add_consultation(factory, status=ConsultationStatus.PENDING, pharmacist_id=None):
    with factory() as db:
        db.add(ConsultationRequest(user_id=PATIENT, chat_summary="두통", status=status, pharmacist_id=pharmacist_id))
        db.commit()


def allowed(factory, pid):
    with factory() as db:
        try:
            patient_info._require_care_relation(db, Actor(id=pid, role="pharmacist"), PATIENT)
            return True
        except HTTPException:
            return False


def test_no_consultation_means_no_access(setup):
    client, factory = setup
    assert client.get(f"/pharmacist/patients/{PATIENT}", headers=headers("약사A")).status_code == 403
    assert client.get(f"/pharmacist/patients/{PATIENT}/safety/123", headers=headers("약사A")).status_code == 403
    assert client.get(f"/pharmacist/patients/{PATIENT}", headers=headers(PATIENT, "user")).status_code == 403


def test_pending_unassigned_consultation_is_open_to_pharmacists(setup):
    _, factory = setup
    add_consultation(factory)
    assert allowed(factory, "약사A") and allowed(factory, "약사B")


def test_assigned_consultation_only_for_that_pharmacist(setup):
    client, factory = setup
    add_consultation(factory, status=ConsultationStatus.APPROVED, pharmacist_id="약사A")
    assert allowed(factory, "약사A")
    assert not allowed(factory, "약사B")
    assert client.get(f"/pharmacist/patients/{PATIENT}", headers=headers("약사B")).status_code == 403


def test_cancelled_consultation_gives_no_access(setup):
    _, factory = setup
    add_consultation(factory, status=ConsultationStatus.CANCELLED)
    assert not allowed(factory, "약사A")


def test_unknown_medication_is_not_shown_as_safe():
    report = SafetyReport(item_seq="1", item_name="약", can_check_interactions=True, unknown_medications=["모르는약"])
    report.findings.append(Finding(Level.UNKNOWN, "병용금기", "성분 정보 없음"))
    assert report.level is Level.UNKNOWN
    assert "확인하지 못했" in report.summary


def test_info_finding_does_not_hide_unknown():
    report = SafetyReport(item_seq="1", item_name="약", can_check_interactions=True, unknown_medications=["모르는약"])
    report.findings.append(Finding(Level.INFO, "참고", "참고 사항"))
    assert report.level is Level.UNKNOWN


def test_fully_checked_without_findings_is_info():
    report = SafetyReport(item_seq="1", item_name="약", can_check_interactions=True, checked_medications=["아는약"])
    assert report.level is Level.INFO
    assert report.summary == "등록하신 정보와 충돌하는 항목은 찾지 못했습니다"


def test_danger_still_wins():
    report = SafetyReport(item_seq="1", item_name="약", can_check_interactions=True, unknown_medications=["x"])
    report.findings.append(Finding(Level.DANGER, "병용금기", "같이 먹으면 안 됨"))
    assert report.level is Level.DANGER
