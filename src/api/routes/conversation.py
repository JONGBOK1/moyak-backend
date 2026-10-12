"""상담 음성 기록·AI 요약 API (한별 구현 이식, 2026-10-12). 로직은 src/consult/conversation.py.

신원: `X-Moyak-User-Id` + `X-Moyak-Role: user|pharmacist` 헤더 (실제 로그인 전 팀 방식 — 요청이 밝힌 ID를 신뢰),
또는 `Authorization: Bearer <토큰>` (CONSULT_AUTH_SECRET 설정 시). 참여자(본인·담당 약사)만 접근할 수 있다.
채팅은 기존 `/consultations/{id}/messages`를 그대로 쓴다.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from src.consult import conversation as service
from src.consult.auth import Actor, current_actor
from src.consult.db import SessionLocal, get_db
from src.consult.models import ConsultationAudio
from src.consult.schemas import ClientID, SummaryContent

router = APIRouter(prefix="/consultations/{cid}", tags=["consultation-recording"])
MAX_AUDIO_BYTES = 20 * 1024 * 1024
AUDIO_TYPES = {"audio/webm": "webm", "audio/wav": "wav", "audio/x-wav": "wav", "audio/ogg": "ogg",
               "audio/mpeg": "mp3", "audio/mp4": "mp4", "audio/x-m4a": "m4a"}


@router.post("/session/claim")
def claim(cid: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    """약사가 상담을 맡는다 (이후 동의·녹음·검토 요약 흐름 사용 가능)."""
    return service.session_view(service.assign(db, cid, actor))


@router.get("/session")
def session(cid: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    return service.session_view(service.participant(db, cid, actor)[1])


@router.post("/session/consent")
def consent(cid: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    """참여자가 '음성 기록 및 AI 요약'에 직접 동의했을 때만 호출. 양측 모두 동의해야 녹음 업로드·요약 가능."""
    return service.consent(db, cid, actor)


@router.post("/audio", status_code=202)
async def upload(cid: str, request: Request, client_id: ClientID, start_ms: int = Query(..., ge=0, le=7200000),
                 actor: Actor = Depends(current_actor)):
    """녹음 파일 업로드(본문 = 음성 바이트). 같은 client_id 재전송은 중복 없이 처리, 실패 건은 재전사."""
    def preflight():  # 큰 본문을 읽기 전에 권한·동의부터 확인
        with SessionLocal() as db:
            _, room = service.participant(db, cid, actor)
            service.active(room)
            service.consented(room)
    await run_in_threadpool(preflight)
    extension = AUDIO_TYPES.get(request.headers.get("content-type", "").split(";")[0].strip())
    if not extension:
        raise HTTPException(415, "webm, ogg, wav, mp3, mp4, m4a 음성을 보내주세요.")
    audio = bytearray()
    async for chunk in request.stream():
        if len(audio) + len(chunk) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "음성 파일은 20 MiB 이하여야 합니다.")
        audio.extend(chunk)
    if not audio:
        raise HTTPException(422, "음성 파일이 비어 있습니다.")

    def save():
        with SessionLocal() as db:
            return service.upload(db, cid, actor, client_id, start_ms, extension, bytes(audio))
    return await run_in_threadpool(save)


@router.get("/audio")
def audio_status(cid: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    service.participant(db, cid, actor)
    return [service.audio_view(row) for row in db.query(ConsultationAudio).filter_by(
        consultation_id=cid).order_by(ConsultationAudio.id).all()]


@router.get("/transcript")
def transcript(cid: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    service.participant(db, cid, actor)
    if actor.role != "pharmacist":
        raise HTTPException(403, "원문 전사는 담당 약사의 검토용입니다.")
    return [{**service.audio_view(row), "text": row.transcript}
            for row in db.query(ConsultationAudio).filter_by(consultation_id=cid).order_by(
                ConsultationAudio.start_ms, ConsultationAudio.id).all()]


@router.post("/session/end")
def end(cid: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    """담당 약사가 상담 종료 → (양측 동의 시) 백그라운드 요약 초안 생성."""
    return service.finish(db, cid, actor)


@router.get("/summary")
def summary(cid: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    """약사: 초안(pending_review)·공개본, 사용자: 공개된 요약만."""
    return service.summary(db, cid, actor)


@router.post("/summary/publish")
def publish(cid: str, payload: SummaryContent, actor: Actor = Depends(current_actor),
            db: Session = Depends(get_db)):
    """약사가 검토·수정한 6항목 요약을 공개 → 사용자 화면에 표시."""
    return service.publish(db, cid, actor, payload)


@router.post("/summary/retry")
def retry(cid: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    return service.retry_summary(db, cid, actor)
