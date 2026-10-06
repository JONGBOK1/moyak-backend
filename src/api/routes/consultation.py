from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy.orm import Session

from src.consult import service
from src.consult.auth import Actor, legacy_actor
from src.consult.db import get_db

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

    model_config = ConfigDict(from_attributes=True)


def _to_response(consultation) -> ConsultationResponse:
    response = ConsultationResponse.model_validate(consultation)
    if consultation.purchase is not None:
        response.approved_purchase_id = consultation.purchase.id
        response.approved_drug_name = consultation.purchase.drug_item_name
    return response


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
) -> list[ConsultationResponse]:
    if actor and actor.role == "user":
        user_id = actor.id
    consultations = service.list_consultations(db, status=status, user_id=user_id)
    if actor and actor.role == "pharmacist":
        consultations = [c for c in consultations if c.pharmacist_id in (None, actor.id)]
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


@router.post("/{consultation_id}/decision", response_model=ConsultationResponse)
def decide_consultation(
    consultation_id: str, payload: ConsultationDecisionRequest, db: Session = Depends(get_db),
    actor: Actor | None = Depends(legacy_actor),
) -> ConsultationResponse:
    if actor and (actor.role != "pharmacist" or actor.id != payload.pharmacist_id):
        raise HTTPException(403, "본인 명의로만 승인할 수 있습니다.")
    try:
        consultation = service.decide_consultation(
            db,
            consultation_id=consultation_id,
            pharmacist_id=payload.pharmacist_id,
            approve=payload.approve,
            reason=payload.reason,
            drug_item_seq=payload.drug_item_seq,
            drug_item_name=payload.drug_item_name,
        )
    except service.NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidStateError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _to_response(consultation)
