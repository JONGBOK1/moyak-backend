from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy.orm import Session

from src.consult import service
from src.consult.auth import Actor, legacy_actor, current_actor, claimed_actor
from src.consult.db import get_db
from src.consult import conversation, video
from src.consult import patient_presence
from src.consult.models import ConsultationSession, ConsultationSummary

router = APIRouter(prefix="/consultations", tags=["consultation"])


class ConsultationCreateRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    chat_summary: str | None = Field(
        None, description="챗봇 상담 대화 요약 (약사가 참고). 챗봇 없이 바로 상담을 시작한 경우 비워도 됨"
    )
    requested_drug_item_seq: str | None = None
    requested_drug_name: str | None = None


class ConsultationDecisionRequest(BaseModel):
    pharmacist_id: str = Field(..., min_length=1)
    approve: bool
    reason: str | None = None
    drug_item_seq: str | None = None
    drug_item_name: str | None = None
    price: int | None = Field(None, ge=0)


class ConsultationResponse(BaseModel):
    id: str
    user_id: str
    chat_summary: str
    room_url: str | None
    requested_drug_item_seq: str | None
    requested_drug_name: str | None
    status: str
    pharmacist_id: str | None
    decision_reason: str | None
    created_at: datetime
    decided_at: datetime | None
    approved_purchase_id: str | None = None
    approved_drug_name: str | None = None
    summary: str | None = None
    ended_at: datetime | None = None
    summary_status: str = 'not_requested'
    patient_online: bool = False
    patient_queue_state: str = 'offline'

    model_config = ConfigDict(from_attributes=True)


def _to_response(consultation) -> ConsultationResponse:
    response = ConsultationResponse.model_validate(consultation)
    if consultation.purchase is not None:
        response.approved_purchase_id = consultation.purchase.id
        response.approved_drug_name = consultation.purchase.drug_item_name
    from sqlalchemy.orm import object_session
    db = object_session(consultation)
    if db is not None:
        room = db.get(ConsultationSession, consultation.id)
        result = db.get(ConsultationSummary, consultation.id)
        response.ended_at = room.ended_at if room else None
        response.patient_online = patient_presence.online(consultation.id)
        response.patient_queue_state = ('ended' if response.ended_at else
            'offline' if not response.patient_online else 'assigned' if room else 'waiting')
        if result:
            response.summary_status = result.status
            # Never expose an unreviewed draft through the legacy endpoint.
            if result.status == 'published' and result.published:
                labels = {'symptoms': '증상', 'discussion': '상담 내용', 'medication_guidance': '복약 안내',
                          'precautions': '주의사항', 'follow_up': '후속 안내', 'needs_verification': '확인 사항'}
                response.summary = '\n\n'.join(label + '\n' + '\n'.join(result.published.get(key, []))
                                               for key, label in labels.items())
    return response


@router.post('/{consultation_id}/heartbeat')
def heartbeat(consultation_id: str, db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    row = get_consultation(consultation_id, db, actor)
    if actor.role != 'user' or actor.id != row.user_id:
        raise HTTPException(403, '환자 본인만 접속 상태를 갱신할 수 있습니다.')
    if row.ended_at or row.status == 'cancelled':
        return {'active': False}
    patient_presence.touch(consultation_id)
    return {'active': True}


@router.get('/{consultation_id}/presence')
def presence(consultation_id: str, db: Session = Depends(get_db), actor: Actor | None = Depends(legacy_actor)):
    row = get_consultation(consultation_id, db, actor)
    count = video.room_participant_count(row.room_url)
    assigned = db.get(ConsultationSession, consultation_id) is not None
    return {'available': count is not None, 'participants': count or 0,
            'pharmacist_in_room': bool(assigned and count and count > 0)}


@router.post('/{consultation_id}/end', response_model=ConsultationResponse)
def end_legacy(consultation_id: str, db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    """상담 종료는 담당 약사만 가능 (사용자 쪽 '통화 종료'는 화면 이탈로 처리하고 이 API를 부르지 않는다)."""
    conversation.finish(db, consultation_id, actor)
    return _to_response(service.get_consultation(db, consultation_id))


@router.post("", response_model=ConsultationResponse)
def create_consultation(payload: ConsultationCreateRequest, db: Session = Depends(get_db),
                        actor: Actor | None = Depends(legacy_actor)) -> ConsultationResponse:
    if actor and (actor.role != "user" or actor.id != payload.user_id):
        raise HTTPException(403, "본인 명의의 상담만 생성할 수 있습니다.")
    consultation = service.create_consultation(
        db,
        user_id=payload.user_id,
        chat_summary=payload.chat_summary,
        requested_drug_item_seq=payload.requested_drug_item_seq,
        requested_drug_name=payload.requested_drug_name,
    )
    return _to_response(consultation)


@router.get("", response_model=list[ConsultationResponse])
def list_consultations(
    status: str | None = None, user_id: str | None = None, db: Session = Depends(get_db),
    actor: Actor | None = Depends(legacy_actor),
    pharmacist_id: str | None = None,
) -> list[ConsultationResponse]:
    if actor and actor.role == "user":
        user_id = actor.id
    consultations = service.list_consultations(db, status=status, user_id=user_id)
    if actor and actor.role == "pharmacist":
        consultations = [c for c in consultations if c.pharmacist_id in (None, actor.id)]
    if pharmacist_id:
        consultations = [c for c in consultations if c.pharmacist_id == pharmacist_id]
    return [_to_response(c) for c in consultations]


@router.get("/{consultation_id}", response_model=ConsultationResponse)
def get_consultation(consultation_id: str, db: Session = Depends(get_db),
                     actor: Actor | None = Depends(legacy_actor)) -> ConsultationResponse:
    """채팅 화면이 자신이 요청한 상담의 상태를 폴링할 때 쓰는 단건 조회."""
    try:
        consultation = service.get_consultation(db, consultation_id)
        if actor and ((actor.role == "user" and actor.id != consultation.user_id)
                      or (actor.role == "pharmacist" and consultation.pharmacist_id not in (None, actor.id))):
            raise HTTPException(403, "해당 상담에 접근할 수 없습니다.")
    except service.NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _to_response(consultation)


@router.post("/{consultation_id}/cancel", response_model=ConsultationResponse)
def cancel_consultation(consultation_id: str, db: Session = Depends(get_db),
                        actor: Actor | None = Depends(legacy_actor)):
    try:
        row = service.get_consultation(db, consultation_id)
        if actor and (actor.role != "user" or row.user_id != actor.id):
            raise HTTPException(403, "본인의 대기 상담만 취소할 수 있습니다.")
        return _to_response(service.cancel_consultation(db, consultation_id))
    except service.NotFoundError as error:
        raise HTTPException(404, str(error)) from error
    except service.InvalidStateError as error:
        raise HTTPException(409, str(error)) from error


@router.post("/{consultation_id}/decision", response_model=ConsultationResponse)
def decide_consultation(
    consultation_id: str, payload: ConsultationDecisionRequest, db: Session = Depends(get_db),
    actor: Actor | None = Depends(legacy_actor),
) -> ConsultationResponse:
    if actor and (actor.role != "pharmacist" or actor.id != payload.pharmacist_id):
        raise HTTPException(403, "본인 명의로만 승인할 수 있습니다.")
    actor = actor or claimed_actor(payload.pharmacist_id, "pharmacist")
    if not db.get(ConsultationSession, consultation_id):
        conversation.assign(db, consultation_id, actor)
    try:
        consultation = service.decide_consultation(
            db,
            consultation_id=consultation_id,
            pharmacist_id=payload.pharmacist_id,
            approve=payload.approve,
            reason=payload.reason,
            drug_item_seq=payload.drug_item_seq,
            drug_item_name=payload.drug_item_name,
            price=payload.price,
        )
    except service.NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidStateError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _to_response(consultation)
