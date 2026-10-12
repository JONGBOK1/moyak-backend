"""상담 음성 기록 동의 · 음성 전사 · 약사 검토 요약 흐름 (한별 구현 이식, 2026-10-12).

흐름: 약사 배정(claim) → 양측 동의(consent) → 각자 마이크 녹음 업로드(audio) → 워커가 전사
     → 약사 승인/거절 → 약사가 상담 종료(session/end) → 워커가 6항목 요약 초안 생성
     → 약사 검토·수정 후 공개(summary/publish) → 공개된 요약만 사용자에게 보임

우리 기존 기능과의 연결
- 채팅은 기존 `/consultations/{id}/messages`(3초 폴링)를 그대로 쓰고, 요약 재료로 함께 넣는다.
- 공개 시 `consultation_requests.summary`(글)·`ended_at`도 채워서, 기존 사용자 화면(대기/화상 요약 카드)에 그대로 표시된다.
- 이 흐름(ConsultationSession)이 시작된 상담은 기존 즉시 요약(`POST /end`)을 막는다 — 검토 없이 공개되면 안 되므로.
"""
import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.orm import Session

from src.consult.auth import Actor
from src.consult.models import (
    ConsultationAudio,
    ConsultationMessage,
    ConsultationRequest,
    ConsultationSession,
    ConsultationStatus,
    ConsultationSummary,
    _now,
)
from src.consult.schemas import SUMMARY_LABELS, SummaryContent

DIRECT_REQUEST_SUMMARY = "직접 상담 요청 (사전 챗봇 대화 없음 — 화상으로 바로 문진 필요)"
MAX_AUDIO_PER_CONSULTATION = 240


def participant(db: Session, cid: str, actor: Actor, *, lock=False):
    if lock:
        # 같은 상담에 대한 동시 변경을 직렬화 (SQLite/PostgreSQL 공통)
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
        raise HTTPException(409, "약사가 아직 상담을 연결하지 않았습니다.")
    return consultation, room


def assign(db: Session, cid: str, actor: Actor):
    """약사가 상담을 맡는다(담당 약사 배정). 같은 약사가 다시 눌러도 그대로 성공."""
    if actor.role != "pharmacist":
        raise HTTPException(403, "약사만 상담을 연결할 수 있습니다.")
    changed = db.execute(update(ConsultationRequest).where(
        ConsultationRequest.id == cid,
        ConsultationRequest.status != ConsultationStatus.CANCELLED,
        ConsultationRequest.ended_at.is_(None),
        (ConsultationRequest.pharmacist_id.is_(None) | (ConsultationRequest.pharmacist_id == actor.id)),
    ).values(pharmacist_id=actor.id)).rowcount
    if not changed:
        raise HTTPException(409, "연결할 수 없는 상담입니다 (취소·종료됐거나 다른 약사가 담당).")
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
        if existing.status == "failed":  # 실패한 파일 재전송 → 다시 전사 대기열로
            active(room)
            consented(room)
            existing.audio, existing.digest = audio, digest
            existing.start_ms, existing.filename = start_ms, f"audio.{extension}"
            existing.status, existing.error = "queued", None
            db.commit()
            return audio_view(existing)
        if existing.digest != digest or existing.start_ms != start_ms:
            raise HTTPException(409, "이미 사용한 client_id입니다.")
        result = audio_view(existing)  # 같은 파일 중복 전송 → 그대로 성공 처리
        db.commit()
        return result
    active(room)
    consented(room)
    if db.query(ConsultationAudio).filter_by(consultation_id=cid).count() >= MAX_AUDIO_PER_CONSULTATION:
        raise HTTPException(409, "상담당 음성 파일 한도를 초과했습니다.")
    row = ConsultationAudio(consultation_id=cid, sender_id=actor.id, sender_role=actor.role,
                            client_id=client_id, start_ms=start_ms, filename=f"audio.{extension}",
                            audio=audio, digest=digest)
    db.add(row)
    db.commit()
    return audio_view(row)


def finish(db, cid, actor):
    """담당 약사가 상담 종료. 양측 동의가 있으면 요약 초안 생성을 예약한다(동의가 없으면 AI 처리 안 함)."""
    consultation, room = participant(db, cid, actor, lock=True)
    if actor.role != "pharmacist":
        raise HTTPException(403, "담당 약사가 상담을 종료해야 합니다.")
    if room.ended_at:
        db.commit()
        return session_view(room)
    audio = db.query(ConsultationAudio).filter_by(consultation_id=cid).order_by(
        ConsultationAudio.start_ms, ConsultationAudio.id).all()
    if any(row.status != "completed" for row in audio):
        raise HTTPException(409, "모든 음성의 전사가 끝난 뒤 종료해주세요. 실패한 파일은 다시 올려주세요.")
    if room.user_consent_at and room.pharmacist_consent_at:
        if consultation.status == ConsultationStatus.PENDING:
            raise HTTPException(409, "약사의 승인 또는 거절을 먼저 확정해주세요.")
        chat = db.query(ConsultationMessage).filter_by(consultation_id=cid).order_by(
            ConsultationMessage.created_at).all()
        prior = consultation.chat_summary if consultation.chat_summary != DIRECT_REQUEST_SUMMARY else ""
        if not audio and not chat and not prior:
            raise HTTPException(409, "요약할 상담 내용이 없습니다.")
        source = {
            "prior_chat": prior,
            "transcript": [{"speaker": a.sender_role, "start_ms": a.start_ms, "text": a.transcript} for a in audio],
            "chat": [{"speaker": m.sender_role, "text": m.content, "time": m.created_at.isoformat()} for m in chat],
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


def summary_text(content: dict) -> str:
    """공개된 6항목 요약 → 기존 사용자 화면(요약 카드)에 보여줄 글."""
    lines = []
    for key, label in SUMMARY_LABELS.items():
        items = content.get(key) or []
        if items:
            lines.append(f"■ {label}")
            lines.extend(f"- {item}" for item in items)
    lines.append("※ 약사가 검토한 요약입니다. 궁금한 점은 약사에게 다시 문의하세요.")
    return "\n".join(lines)


def publish(db, cid, actor, content: SummaryContent):
    consultation, _ = participant(db, cid, actor, lock=True)
    if actor.role != "pharmacist":
        raise HTTPException(403, "담당 약사만 요약을 확정할 수 있습니다.")
    row = db.get(ConsultationSummary, cid)
    if not row or row.status != "pending_review":
        raise HTTPException(409, "확인 대기 중인 요약이 없습니다.")
    row.published = content.model_dump()
    row.reviewed_by = actor.id
    row.reviewed_at = _now()
    row.status = "published"
    # 기존 사용자 화면(대기 화면 요약 카드, 화상 화면 요약)이 그대로 보여주도록 기존 필드에도 저장
    consultation.summary = summary_text(row.published)
    consultation.ended_at = _now()
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
