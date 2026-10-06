import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from src.consult import conversation as service
from src.consult.auth import Actor, current_actor, verify_token
from src.consult.db import get_db, SessionLocal
from src.consult.models import ConsultationAudio
from src.consult.schemas import ClientID, MessageCreate, SummaryContent

router = APIRouter(prefix="/consultations/{cid}", tags=["consultation-conversation"])
MAX_AUDIO_BYTES = 20 * 1024 * 1024
AUDIO_TYPES = {"audio/webm": "webm", "audio/wav": "wav", "audio/x-wav": "wav",
               "audio/mpeg": "mp3", "audio/mp4": "mp4", "audio/x-m4a": "m4a"}


@router.post("/session/claim")
def claim(cid: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    return service.session_view(service.assign(db, cid, actor))


@router.get("/session")
def session(cid: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    return service.session_view(service.participant(db, cid, actor)[1])


@router.post("/session/consent")
def consent(cid: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    """Call only after this participant explicitly accepts recording + AI processing."""
    return service.consent(db, cid, actor)


@router.post("/messages", status_code=201)
def send(cid: str, payload: MessageCreate, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    return service.send(db, cid, actor, payload)


@router.get("/messages")
def messages(cid: str, after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200),
             actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    return service.messages(db, cid, actor, after, limit)


@router.post("/audio", status_code=202)
async def upload(cid: str, request: Request, client_id: ClientID, start_ms: int = Query(..., ge=0, le=7200000),
                 actor: Actor = Depends(current_actor)):
    # Validate access BEFORE reading an untrusted, potentially large request body.
    def preflight():
        with SessionLocal() as db:
            _, room = service.participant(db, cid, actor)
            service.active(room)
            service.consented(room)
    await run_in_threadpool(preflight)
    extension = AUDIO_TYPES.get(request.headers.get("content-type", "").split(";")[0].strip())
    if not extension:
        raise HTTPException(415, "webm, wav, mp3, mp4, m4a 음성을 보내주세요.")
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
    return service.finish(db, cid, actor)


@router.get("/summary")
def summary(cid: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    return service.summary(db, cid, actor)


@router.post("/summary/publish")
def publish(cid: str, payload: SummaryContent, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    return service.publish(db, cid, actor, payload)


@router.post("/summary/retry")
def retry(cid: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    return service.retry_summary(db, cid, actor)


@router.websocket("/messages/ws")
async def socket(websocket: WebSocket, cid: str):
    """First frame authenticates; DB-backed fanout works across API processes."""
    await websocket.accept()
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=10)
        if len(raw) > 6000:
            await websocket.close(code=1009)
            return
        hello = json.loads(raw)
        token = hello["token"]
        actor = verify_token(token)
        cursor = hello.get("after", 0)
        if type(cursor) is not int or cursor < 0:
            raise ValueError()

        def fetch(after):
            with SessionLocal() as db:
                return service.messages(db, cid, actor, after, 200)

        def save(payload):
            with SessionLocal() as db:
                return service.send(db, cid, actor, payload)

        while True:
            verify_token(token)  # Expired credentials also terminate existing sockets.
            rows = await run_in_threadpool(fetch, cursor)
            if rows:
                await websocket.send_json(jsonable_encoder({"type": "messages", "items": rows}))
                cursor = rows[-1]["id"]
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if len(raw) > 20000:
                await websocket.close(code=1009)
                return
            try:
                payload = MessageCreate.model_validate_json(raw)
                message = await run_in_threadpool(save, payload)
                await websocket.send_json(jsonable_encoder({"type": "ack", "message": message}))
            except (ValidationError, HTTPException) as error:
                await websocket.send_json({"type": "error", "detail": (
                    error.detail if isinstance(error, HTTPException) else "메시지 형식이 올바르지 않습니다.")})
    except WebSocketDisconnect:
        pass
    except (HTTPException, ValueError, KeyError, TypeError, asyncio.TimeoutError):
        await websocket.close(code=1008)
