from datetime import timedelta

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src import config
from src.api.routes import conversation as routes, consultation as legacy
from src.consult import conversation as service, ai, worker
from src.consult.auth import Actor, issue_token, verify_token
from src.consult.db import Base, get_db
from src.consult.models import (ConsultationRequest, ConsultationAudio, ConsultationSummary, _now)
from src.consult.schemas import MessageCreate, SummaryContent

USER = Actor("u1", "user")
PHARM = Actor("p1", "pharmacist")
CONTENT = dict(symptoms=["두통"], discussion=[], medication_guidance=[],
               precautions=[], follow_up=[], needs_verification=["용량 확인 필요"])


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(config, "CONSULT_AUTH_SECRET", "test-secret-" * 4)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(ConsultationRequest(id="c1", user_id="u1", chat_summary="test"))
        db.commit()
        service.assign(db, "c1", PHARM)
    app = FastAPI()
    app.include_router(routes.router)
    app.include_router(legacy.router)
    def dependency():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = dependency
    monkeypatch.setattr(routes, "SessionLocal", factory)
    monkeypatch.setattr(ai, "transcribe", lambda *args: "머리가 아파요")
    monkeypatch.setattr(ai, "summarize", lambda source: CONTENT)
    with TestClient(app) as client:
        yield client, factory
    engine.dispose()


def headers(actor=USER):
    return {"Authorization": f"Bearer {issue_token(actor.id, actor.role)}"}


def consent(client):
    for actor in (USER, PHARM):
        assert client.post("/consultations/c1/session/consent", headers=headers(actor)).status_code == 200


def decision(client):
    response = client.post("/consultations/c1/decision", headers=headers(PHARM),
                           json={"pharmacist_id": "p1", "approve": False, "reason": "대면 진료 안내"})
    assert response.status_code == 200


def test_auth_and_participant_isolation(setup):
    client, _ = setup
    assert client.get("/consultations/c1/messages").status_code == 401
    assert client.get("/consultations/c1/messages", headers=headers(Actor("u2", "user"))).status_code == 403
    assert client.get("/consultations/c1/messages", headers=headers(Actor("p2", "pharmacist"))).status_code == 403
    assert client.post("/consultations/c1/session/claim", headers=headers(Actor("p2", "pharmacist"))).status_code == 409
    assert client.post("/consultations/c1/session/claim", headers=headers(USER)).status_code == 403
    assert client.get("/consultations/c1", headers=headers(Actor("u2", "user"))).status_code == 403
    assert client.post("/consultations/c1/decision", headers=headers(USER),
                       json={"pharmacist_id": "p1", "approve": False}).status_code == 403


def test_token_tamper_expiry_and_configuration(setup, monkeypatch):
    token = issue_token(USER.id, USER.role)
    assert verify_token(token) == USER
    with pytest.raises(HTTPException):
        verify_token(token + "x")
    with monkeypatch.context() as patch:
        patch.setattr("src.consult.auth.time.time", lambda: 10**12)
        with pytest.raises(HTTPException):
            verify_token(token)
    monkeypatch.setattr(config, "CONSULT_AUTH_SECRET", "")
    with pytest.raises(HTTPException) as error:
        verify_token(token)
    assert error.value.status_code == 503


def test_chat_idempotency_validation_pagination_and_history(setup):
    client, _ = setup
    path = "/consultations/c1/messages"
    payload = {"client_id": "m1", "text": "복용법을 알려주세요"}
    first = client.post(path, headers=headers(), json=payload)
    assert first.status_code == 201
    assert client.post(path, headers=headers(), json=payload).json()["id"] == first.json()["id"]
    assert client.post(path, headers=headers(), json={**payload, "text": "changed"}).status_code == 409
    assert client.post(path, headers=headers(), json={"client_id": "m2", "text": "  "}).status_code == 422
    second = client.post(path, headers=headers(PHARM), json={"client_id": "m2", "text": "안내입니다"})
    rows = client.get(path, headers=headers(), params={"after": first.json()["id"]}).json()
    assert [row["id"] for row in rows] == [second.json()["id"]]
    assert client.post("/consultations/c1/session/end", headers=headers(PHARM)).status_code == 200
    assert client.post(path, headers=headers(), json={"client_id": "m3", "text": "종료 후"}).status_code == 409
    assert len(client.get(path, headers=headers()).json()) == 2
    assert client.get("/consultations/c1/summary", headers=headers()).json()["status"] == "not_requested"


def test_audio_to_reviewed_summary(setup):
    client, factory = setup
    path = "/consultations/c1/audio?client_id=a1&start_ms=0"
    audio_headers = {**headers(), "Content-Type": "audio/webm"}
    assert client.post(path, headers=audio_headers, content=b"audio").status_code == 409
    consent(client)
    assert client.post(path, headers=audio_headers, content=b"audio").status_code == 202
    assert client.post(path, headers=audio_headers, content=b"audio").status_code == 202
    assert client.post("/consultations/c1/session/end", headers=headers(PHARM)).status_code == 409
    assert worker.process_one(factory)
    with factory() as db:
        audio = db.query(ConsultationAudio).one()
        assert audio.status == "completed" and audio.audio is None
    assert client.get("/consultations/c1/transcript", headers=headers()).status_code == 403
    assert client.get("/consultations/c1/transcript", headers=headers(PHARM)).json()[0]["text"] == "머리가 아파요"
    decision(client)
    assert client.post("/consultations/c1/session/end", headers=headers(PHARM)).status_code == 200
    assert client.post("/consultations/c1/session/end", headers=headers(PHARM)).status_code == 200
    assert worker.process_one(factory)
    hidden = client.get("/consultations/c1/summary", headers=headers()).json()
    assert hidden["status"] == "pending_review" and hidden["content"] is None
    draft = client.get("/consultations/c1/summary", headers=headers(PHARM)).json()
    assert draft["content"] == CONTENT
    assert client.post("/consultations/c1/summary/publish", headers=headers(), json=CONTENT).status_code == 403
    corrected = {**CONTENT, "discussion": ["약사가 확인한 내용"]}
    assert client.post("/consultations/c1/summary/publish", headers=headers(PHARM), json=corrected).status_code == 200
    published = client.get("/consultations/c1/summary", headers=headers()).json()
    assert published["content"] == corrected and published["reviewed_by"] == "p1"
    assert client.post("/consultations/c1/summary/retry", headers=headers(PHARM)).status_code == 409


def test_failures_reupload_summary_retry_and_lease_recovery(setup, monkeypatch):
    client, factory = setup
    consent(client)
    path = "/consultations/c1/audio?client_id=a1&start_ms=0"
    audio_headers = {**headers(), "Content-Type": "audio/webm"}
    client.post(path, headers=audio_headers, content=b"audio")
    def fail(*args):
        raise RuntimeError("private provider response must not escape")
    monkeypatch.setattr(ai, "transcribe", fail)
    assert worker.process_one(factory)
    with factory() as db:
        row = db.query(ConsultationAudio).one()
        assert row.audio is None and row.error == "ai_processing_failed"
    assert client.post(path, headers=audio_headers, content=b"new-audio").json()["status"] == "queued"
    monkeypatch.setattr(ai, "transcribe", lambda *args: "두통 상담")
    assert worker.process_one(factory)
    decision(client)
    client.post("/consultations/c1/session/end", headers=headers(PHARM))
    monkeypatch.setattr(ai, "summarize", fail)
    assert worker.process_one(factory)
    assert client.get("/consultations/c1/summary", headers=headers()).json()["status"] == "failed"
    assert client.post("/consultations/c1/summary/retry", headers=headers(PHARM)).status_code == 200
    with factory() as db:
        row = db.get(ConsultationSummary, "c1")
        row.status, row.lease, row.started_at = "processing", "crashed", _now() - timedelta(minutes=6)
        db.commit()
    monkeypatch.setattr(ai, "summarize", lambda source: CONTENT)
    assert worker.process_one(factory)
    assert client.get("/consultations/c1/summary", headers=headers(PHARM)).json()["status"] == "pending_review"
    assert not worker.process_one(factory)


def test_websocket_send_replay_and_unauthorized(setup):
    client, _ = setup
    with client.websocket_connect("/consultations/c1/messages/ws") as socket:
        socket.send_json({"token": issue_token(USER.id, USER.role), "after": 0})
        socket.send_json({"client_id": "ws1", "text": "실시간 메시지"})
        ack = socket.receive_json()
        assert ack["type"] == "ack"
        event = socket.receive_json()
        assert event["type"] == "messages" and event["items"][0]["text"] == "실시간 메시지"
    with client.websocket_connect("/consultations/c1/messages/ws") as socket:
        socket.send_json({"token": issue_token(PHARM.id, PHARM.role), "after": 0})
        assert socket.receive_json()["items"][0]["client_id"] == "ws1"
    with client.websocket_connect("/consultations/c1/messages/ws") as socket:
        socket.send_json({"token": issue_token("u2", "user")})
        assert socket.receive()["code"] == 1008


def test_audio_bounds_and_empty_summary(setup, monkeypatch):
    client, _ = setup
    consent(client)
    assert client.post("/consultations/c1/session/end", headers=headers(USER)).status_code == 403
    path = "/consultations/c1/audio?client_id=a1&start_ms=0"
    assert client.post(path, headers=headers(), content=b"data").status_code == 415
    audio_headers = {**headers(), "Content-Type": "audio/webm"}
    assert client.post(path, headers=audio_headers, content=b"").status_code == 422
    monkeypatch.setattr(routes, "MAX_AUDIO_BYTES", 3)
    assert client.post(path, headers=audio_headers, content=b"long").status_code == 413
    decision(client)
    assert client.post("/consultations/c1/session/end", headers=headers(PHARM)).status_code == 409


def test_expired_worker_cannot_overwrite_new_owner(setup, monkeypatch):
    client, factory = setup
    consent(client)
    client.post("/consultations/c1/messages", headers=headers(), json={"client_id": "m1", "text": "두통 상담"})
    decision(client)
    assert client.post("/consultations/c1/session/end", headers=headers(PHARM)).status_code == 200
    def simulate_reclaim(source):
        with factory() as db:
            row = db.get(ConsultationSummary, "c1")
            row.lease, row.status = "new-owner", "processing"
            db.commit()
        return CONTENT
    monkeypatch.setattr(ai, "summarize", simulate_reclaim)
    assert worker.process_one(factory)
    with factory() as db:
        row = db.get(ConsultationSummary, "c1")
        assert row.lease == "new-owner" and row.status == "processing" and row.draft is None


def test_other_pharmacist_cannot_decide_or_list_assigned_consultation(setup):
    client, _ = setup
    other = Actor("p2", "pharmacist")
    assert client.get("/consultations", headers=headers(other)).json() == []
    assert client.post("/consultations/c1/decision", headers=headers(other),
                       json={"pharmacist_id": "p2", "approve": False}).status_code == 409
