"""상담 음성 기록·검토 요약 흐름 (한별 구현 이식) — AI는 모의 처리, 실제 API 호출 없음."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.routes import consultation as legacy
from src.api.routes import conversation as routes
from src.consult import ai, service, worker
from src.consult.db import Base, get_db
from src.consult.models import ConsultationAudio, ConsultationRequest

CONTENT = dict(symptoms=["두통"], discussion=["어제부터 머리가 아픔"], medication_guidance=["4~6시간 간격 1정"],
               precautions=["음주 금지"], follow_up=[], needs_verification=["용량 확인 필요"])
USER = {"X-Moyak-User-Id": "u1", "X-Moyak-Role": "user"}
PHARM = {"X-Moyak-User-Id": "p1", "X-Moyak-Role": "pharmacist"}


@pytest.fixture
def setup(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(ConsultationRequest(id="c1", user_id="u1", chat_summary="사용자: 두통이 있어요"))
        db.commit()
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


def run_worker(factory):
    while worker.process_one(factory):
        pass


def test_participant_isolation_and_claim(setup):
    client, _ = setup
    assert client.get("/consultations/c1/session").status_code == 401  # 신원 없음
    assert client.get("/consultations/c1/session", headers=USER).status_code == 409  # 약사 미배정
    assert client.post("/consultations/c1/session/claim", headers=USER).status_code == 403  # 사용자는 배정 불가
    assert client.post("/consultations/c1/session/claim", headers=PHARM).status_code == 200
    assert client.post("/consultations/c1/session/claim", headers=PHARM).status_code == 200  # 같은 약사 재연결 OK
    other = {"X-Moyak-User-Id": "p2", "X-Moyak-Role": "pharmacist"}
    assert client.post("/consultations/c1/session/claim", headers=other).status_code == 409  # 다른 약사 불가
    stranger = {"X-Moyak-User-Id": "u2", "X-Moyak-Role": "user"}
    assert client.get("/consultations/c1/session", headers=stranger).status_code == 403


def test_audio_requires_both_consents(setup):
    client, _ = setup
    client.post("/consultations/c1/session/claim", headers=PHARM)
    up = lambda h: client.post("/consultations/c1/audio?client_id=a1&start_ms=0", headers={**h, "Content-Type": "audio/webm"}, content=b"voice")
    assert up(USER).status_code == 409  # 동의 전
    client.post("/consultations/c1/session/consent", headers=USER)
    assert up(USER).status_code == 409  # 한쪽만 동의
    client.post("/consultations/c1/session/consent", headers=PHARM)
    assert up(USER).status_code == 202
    assert up(USER).json()["status"] == "queued"  # 같은 client_id 재전송은 중복 없이 성공


def test_full_flow_review_then_publish(setup):
    client, factory = setup
    client.post("/consultations/c1/session/claim", headers=PHARM)
    for h in (USER, PHARM):
        client.post("/consultations/c1/session/consent", headers=h)
    client.post("/consultations/c1/audio?client_id=a1&start_ms=0", headers={**USER, "Content-Type": "audio/webm"}, content=b"voice")
    client.post("/consultations/c1/messages", json={"sender_role": "pharmacist", "sender_id": "p1", "content": "언제부터요?"})

    assert client.post("/consultations/c1/session/end", headers=PHARM).status_code == 409  # 전사 미완료
    run_worker(factory)
    assert client.get("/consultations/c1/transcript", headers=PHARM).json()[0]["text"] == "머리가 아파요"
    assert client.post("/consultations/c1/session/end", headers=PHARM).status_code == 409  # 승인/거절 전
    client.post("/consultations/c1/decision", json={"pharmacist_id": "p1", "approve": False, "reason": "대면 진료"})
    assert client.post("/consultations/c1/session/end", headers=USER).status_code == 403  # 사용자는 종료 불가
    assert client.post("/consultations/c1/session/end", headers=PHARM).status_code == 200

    # 종료 후 채팅 불가, 기존 즉시 요약(/end)도 막힘
    assert client.post("/consultations/c1/messages", json={"sender_role": "user", "sender_id": "u1", "content": "x"}).status_code == 409

    run_worker(factory)  # 요약 초안 생성
    draft = client.get("/consultations/c1/summary", headers=PHARM).json()
    assert draft["status"] == "pending_review" and draft["content"]["symptoms"] == ["두통"]
    assert client.get("/consultations/c1/summary", headers=USER).json()["content"] is None  # 공개 전엔 사용자에게 안 보임
    assert client.get("/consultations/c1").json()["summary"] is None

    edited = {**CONTENT, "medication_guidance": ["약사 수정: 대면 진료 권고"]}
    assert client.post("/consultations/c1/summary/publish", json=edited, headers=USER).status_code == 403
    assert client.post("/consultations/c1/summary/publish", json=edited, headers=PHARM).status_code == 200
    assert client.get("/consultations/c1/summary", headers=USER).json()["content"]["medication_guidance"] == ["약사 수정: 대면 진료 권고"]
    legacy_view = client.get("/consultations/c1").json()  # 기존 화면이 쓰는 필드에도 반영
    assert "■ 복약 안내" in legacy_view["summary"] and "약사 수정" in legacy_view["summary"]
    assert legacy_view["ended_at"] is not None


def test_failed_transcription_can_be_reuploaded_and_summary_retried(setup, monkeypatch):
    client, factory = setup
    client.post("/consultations/c1/session/claim", headers=PHARM)
    for h in (USER, PHARM):
        client.post("/consultations/c1/session/consent", headers=h)

    def fail(*a):
        raise RuntimeError("provider down")
    monkeypatch.setattr(ai, "transcribe", fail)
    client.post("/consultations/c1/audio?client_id=a1&start_ms=0", headers={**USER, "Content-Type": "audio/webm"}, content=b"voice")
    run_worker(factory)
    assert client.get("/consultations/c1/audio", headers=USER).json()[0]["status"] == "failed"

    monkeypatch.setattr(ai, "transcribe", lambda *a: "다시 올린 음성")
    retry = client.post("/consultations/c1/audio?client_id=a1&start_ms=0", headers={**USER, "Content-Type": "audio/webm"}, content=b"voice")
    assert retry.status_code == 202 and retry.json()["status"] == "queued"
    run_worker(factory)
    with factory() as db:
        assert db.query(ConsultationAudio).one().transcript == "다시 올린 음성"
        assert db.query(ConsultationAudio).one().audio is None  # 전사 후 원본 음성 삭제

    client.post("/consultations/c1/decision", json={"pharmacist_id": "p1", "approve": False})
    client.post("/consultations/c1/session/end", headers=PHARM)
    monkeypatch.setattr(ai, "summarize", lambda s: (_ for _ in ()).throw(RuntimeError("x")))
    run_worker(factory)
    assert client.get("/consultations/c1/summary", headers=PHARM).json()["status"] == "failed"
    monkeypatch.setattr(ai, "summarize", lambda s: CONTENT)
    assert client.post("/consultations/c1/summary/retry", headers=PHARM).json()["status"] == "queued"
    run_worker(factory)
    assert client.get("/consultations/c1/summary", headers=PHARM).json()["status"] == "pending_review"


def test_legacy_immediate_summary_blocked_once_pharmacist_connected(setup):
    client, factory = setup
    client.post("/consultations/c1/session/claim", headers=PHARM)
    with factory() as db:
        with pytest.raises(service.InvalidStateError):
            service.end_consultation(db, "c1", llm=None)


def test_no_consent_ends_without_ai_summary(setup):
    client, _ = setup
    client.post("/consultations/c1/session/claim", headers=PHARM)
    client.post("/consultations/c1/decision", json={"pharmacist_id": "p1", "approve": False})
    assert client.post("/consultations/c1/session/end", headers=PHARM).status_code == 200
    assert client.get("/consultations/c1/summary", headers=PHARM).json()["status"] == "not_requested"


def test_auto_recording_segments_time_base_and_failed_segments(setup, monkeypatch):
    client, factory = setup
    client.post("/consultations/c1/session/claim", headers=PHARM)
    client.post("/consultations/c1/session/consent", headers=USER)
    assert client.get("/consultations/c1/session", headers=USER).json()["recording_elapsed_ms"] is None  # 한쪽 동의
    client.post("/consultations/c1/session/consent", headers=PHARM)
    assert client.get("/consultations/c1/session", headers=USER).json()["recording_elapsed_ms"] >= 0  # 공통 시간 기준

    texts = {b"u0": "어제부터 머리가 아파요", b"p20": "열은 있으세요?", b"u20": "", b"u40": None}

    def fake(filename, audio):
        if texts[audio] is None:
            raise RuntimeError("provider down")
        return texts[audio]
    monkeypatch.setattr(ai, "transcribe", fake)
    for h, cid, ms, body in ((USER, "u0", 0, b"u0"), (PHARM, "p20", 20000, b"p20"),
                             (USER, "u20", 20000, b"u20"), (USER, "u40", 40000, b"u40")):
        client.post(f"/consultations/c1/audio?client_id={cid}&start_ms={ms}",
                    headers={**h, "Content-Type": "audio/webm"}, content=body)
    client.post("/consultations/c1/decision", json={"pharmacist_id": "p1", "approve": False})
    assert client.post("/consultations/c1/session/end", headers=PHARM).status_code == 409  # 전사 중이면 대기
    run_worker(factory)
    # 실패한 조각(상대가 이미 나가 재전송 불가)이 있어도 종료 가능, 요약 한계로 명시
    assert client.post("/consultations/c1/session/end", headers=PHARM).status_code == 200
    from src.consult.models import ConsultationSummary
    with factory() as db:
        source = db.get(ConsultationSummary, "c1").source
    assert [t["text"] for t in source["transcript"]] == ["어제부터 머리가 아파요", "열은 있으세요?"]  # 시간순, 빈 조각 제외
    assert any("1개는 전사에 실패" in x for x in source["limitations"])
