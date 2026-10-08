"""Persisted chat, recording consent and pharmacist-reviewed summary lifecycle."""
import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.orm import Session

from src.consult.auth import Actor
from src.consult.models import (ConsultationRequest, ConsultationSession, ConsultationMessage,
                                ConsultationAudio, ConsultationSummary, _now)
from src.consult.schemas import MessageCreate, SummaryContent


def participant(db: Session, cid: str, actor: Actor, *, lock=False):
    if lock:
        # Serialize mutations of a room on SQLite as well as PostgreSQL.
        db.execute(update(ConsultationSession).where(
            ConsultationSession.consultation_id == cid
        ).values(revision=ConsultationSession.revision + 1))
    consultation = db.get(ConsultationRequest, cid, populate_existing=True)
    room = db.get(ConsultationSession, cid, populate_existing=True)
    if not consultation:
        raise HTTPException(404, "상담을 찾을 수 없습니다.")
    allowed = ((actor.role == "user" and actor.id == consultation.user_id)
               or (actor.role == "pharmacist" and room and actor.id == room.pharmacist_id))
    if not allowed:
        raise HTTPException(403, "상담 참여자만 접근할 수 있습니다.")
    if not room:
        raise HTTPException(409, "약사 배정이 필요합니다.")
    return consultation, room


def assign(db: Session, cid: str, actor: Actor):
    if actor.role != "pharmacist":
        raise HTTPException(403, "약사만 상담을 배정받을 수 있습니다.")
    # This also locks the parent row to make duplicate assignments idempotent.
    changed = db.execute(update(ConsultationRequest).where(
        ConsultationRequest.id == cid,
        ConsultationRequest.status == "pending",
        (ConsultationRequest.pharmacist_id.is_(None) | (ConsultationRequest.pharmacist_id == actor.id)),
    ).values(pharmacist_id=actor.id)).rowcount
    if not changed:
        raise HTTPException(409, "배정할 수 없는 상담입니다.")
    room = db.get(ConsultationSession, cid)
    if not room:
        room = ConsultationSession(consultation_id=cid, pharmacist_id=actor.id)
        db.add(room)
    elif room.pharmacist_id != actor.id:
        raise HTTPException(409, "이미 다른 약사에게 배정되었습니다.")
    db.commit()
    return room


def active(room):
    if room.ended_at:
        raise HTTPException(409, "종료된 상담입니다.")


def consented(room):
    if not room.user_consent_at or not room.pharmacist_consent_at:
        raise HTTPException(409, "양측의 음성 기록 및 AI 요약 동의가 필요합니다.")


def session_view(room):
    return {key: getattr(room, key) for key in (
        "consultation_id", "pharmacist_id", "user_consent_at", "pharmacist_consent_at", "ended_at")}


def consent(db, cid, actor):
    _, room = participant(db, cid, actor, lock=True)
    active(room)
    field = "user_consent_at" if actor.role == "user" else "pharmacist_consent_at"
    if getattr(room, field) is None:
        setattr(room, field, _now())
    db.commit()
    return session_view(room)


def message_view(message):
    return {**{key: getattr(message, key) for key in (
        "id", "consultation_id", "sender_id", "sender_role", "client_id", "text", "created_at")},
        "content": message.text}


def messages(db, cid, actor, after=0, limit=100):
    if actor is None:
        # 팀 규격(신원 없이 폴링) 호환: 상담 존재 여부만 확인한다.
        if not db.get(ConsultationRequest, cid):
            raise HTTPException(404, "상담을 찾을 수 없습니다.")
    else:
        participant(db, cid, actor)
    rows = db.query(ConsultationMessage).filter(
        ConsultationMessage.consultation_id == cid, ConsultationMessage.id > after,
    ).order_by(ConsultationMessage.id).limit(limit).all()
    return [message_view(row) for row in rows]


def send(db, cid, actor, payload: MessageCreate):
    if ((payload.sender_id is not None and payload.sender_id != actor.id)
            or (payload.sender_role is not None and payload.sender_role != actor.role)):
        raise HTTPException(403, '메시지 발신자가 인증 사용자와 다릅니다.')
    if actor.role == "pharmacist" and not db.get(ConsultationSession, cid):
        assign(db, cid, actor)  # 팀 흐름: 약사가 채팅을 시작하면 그 상담을 맡은 것으로 본다.
    _, room = participant(db, cid, actor, lock=True)
    existing = db.query(ConsultationMessage).filter_by(
        consultation_id=cid, sender_id=actor.id, sender_role=actor.role, client_id=payload.client_id,
    ).first()
    if existing:
        if existing.text != payload.text:
            raise HTTPException(409, "이미 사용한 client_id입니다.")
        result = message_view(existing)
        db.commit()
        return result
    active(room)
    message = ConsultationMessage(consultation_id=cid, sender_id=actor.id,
                                  sender_role=actor.role, **payload.model_dump(include={'client_id', 'text'}))
    db.add(message)
    db.commit()
    return message_view(message)


def audio_view(row):
    return {key: getattr(row, key) for key in (
        "id", "client_id", "sender_role", "start_ms", "status", "error")}


def upload(db, cid, actor, client_id, start_ms, extension, audio):
    _, room = participant(db, cid, actor, lock=True)
    digest = hashlib.sha256(audio).hexdigest()
    existing = db.query(ConsultationAudio).filter_by(
        consultation_id=cid, sender_id=actor.id, sender_role=actor.role, client_id=client_id,
    ).first()
    if existing:
        if existing.status == "failed":
            active(room)
            consented(room)
            existing.audio, existing.digest = audio, digest
            existing.start_ms, existing.filename = start_ms, f"audio.{extension}"
            existing.status, existing.error = "queued", None
            db.commit()
            return audio_view(existing)
        if existing.digest != digest or existing.start_ms != start_ms:
            raise HTTPException(409, "이미 사용한 client_id입니다.")
        result = audio_view(existing)
        db.commit()
        return result
    active(room)
    consented(room)
    count = db.query(ConsultationAudio).filter_by(consultation_id=cid).count()
    if count >= 240:
        raise HTTPException(409, "상담당 음성 파일 한도를 초과했습니다.")
    row = ConsultationAudio(consultation_id=cid, sender_id=actor.id, sender_role=actor.role,
                            client_id=client_id, start_ms=start_ms, filename=f"audio.{extension}",
                            audio=audio, digest=digest)
    db.add(row)
    db.commit()
    return audio_view(row)


def finish(db, cid, actor):
    consultation, room = participant(db, cid, actor, lock=True)
    if actor.role != "pharmacist":
        raise HTTPException(403, "담당 약사가 상담을 종료해야 합니다.")
    if room.ended_at:
        db.commit()
        return session_view(room)
    audio = db.query(ConsultationAudio).filter_by(consultation_id=cid).order_by(
        ConsultationAudio.start_ms, ConsultationAudio.id).all()
    if any(row.status != "completed" for row in audio):
        raise HTTPException(409, "모든 음성의 전사가 완료된 뒤 종료해주세요. 실패한 파일은 재업로드해주세요.")
    # No consent means chat remains usable, but no external AI processing occurs.
    if room.user_consent_at and room.pharmacist_consent_at:
        if consultation.status == "pending":
            raise HTTPException(409, "약사의 승인 또는 거절을 확정한 뒤 종료해주세요.")
        chat = db.query(ConsultationMessage).filter_by(consultation_id=cid).order_by(ConsultationMessage.id).all()
        if not audio and not chat:
            raise HTTPException(409, "요약할 상담 내용이 없습니다.")
        source = {
            "prior_chat": consultation.chat_summary if consultation.chat_summary !=
                "직접 상담 요청 (사전 챗봇 대화 없음 — 화상으로 바로 문진 필요)" else "",
            "transcript": [{"speaker": a.sender_role, "start_ms": a.start_ms, "text": a.transcript} for a in audio],
            "chat": [{"speaker": m.sender_role, "text": m.text, "time": m.created_at.isoformat()} for m in chat],
            "decision": {"status": consultation.status, "reason": consultation.decision_reason,
                         "approved_drug": consultation.purchase.drug_item_name if consultation.purchase else None},
            "limitations": ["제출된 음성/채팅만 포함합니다. 업로드하지 않은 대화는 확인할 수 없습니다."],
        }
        if len(json.dumps(source, ensure_ascii=False)) > 120000:
            raise HTTPException(413, "요약 입력 한도를 초과했습니다.")
        db.add(ConsultationSummary(consultation_id=cid, source=source))
    room.ended_at = _now()
    db.commit()
    return session_view(room)


def summary(db, cid, actor):
    participant(db, cid, actor)
    row = db.get(ConsultationSummary, cid)
    if not row:
        return {"status": "not_requested", "content": None}
    return {"status": row.status, "content": row.published if row.status == "published" else (
        row.draft if actor.role == "pharmacist" else None),
        "reviewed_by": row.reviewed_by, "reviewed_at": row.reviewed_at, "error": row.error}


def publish(db, cid, actor, content: SummaryContent):
    participant(db, cid, actor, lock=True)
    if actor.role != "pharmacist":
        raise HTTPException(403, "담당 약사만 요약을 확정할 수 있습니다.")
    row = db.get(ConsultationSummary, cid)
    if not row or row.status != "pending_review":
        raise HTTPException(409, "확인 대기 중인 요약이 없습니다.")
    row.published = content.model_dump()
    row.reviewed_by = actor.id
    row.reviewed_at = _now()
    row.status = "published"
    db.commit()
    return summary(db, cid, actor)


def retry_summary(db, cid, actor):
    participant(db, cid, actor, lock=True)
    if actor.role != "pharmacist":
        raise HTTPException(403, "담당 약사만 재시도할 수 있습니다.")
    row = db.get(ConsultationSummary, cid)
    if not row or row.status != "failed":
        raise HTTPException(409, "실패한 요약만 재시도할 수 있습니다.")
    row.status, row.error = "queued", None
    db.commit()
    return summary(db, cid, actor)
