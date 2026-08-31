from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.consult import service
from src.consult.db import get_db

router = APIRouter(prefix="/consultations", tags=["consultation"])


class ConsultationCreateRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    chat_summary: str = Field(..., min_length=1, description="챗봇 상담 대화 요약 (약사가 참고)")
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
    requested_drug_item_seq: str | None
    requested_drug_name: str | None
    status: str
    pharmacist_id: str | None
    decision_reason: str | None
    created_at: datetime
    decided_at: datetime | None
    approved_purchase_id: str | None = None
    approved_drug_name: str | None = None

    class Config:
        from_attributes = True


def _to_response(consultation) -> ConsultationResponse:
    response = ConsultationResponse.model_validate(consultation)
    if consultation.purchase is not None:
        response.approved_purchase_id = consultation.purchase.id
        response.approved_drug_name = consultation.purchase.drug_item_name
    return response


@router.post("", response_model=ConsultationResponse)
def create_consultation(payload: ConsultationCreateRequest, db: Session = Depends(get_db)) -> ConsultationResponse:
    consultation = service.create_consultation(
        db,
        user_id=payload.user_id,
        chat_summary=payload.chat_summary,
        requested_drug_item_seq=payload.requested_drug_item_seq,
        requested_drug_name=payload.requested_drug_name,
    )
    return _to_response(consultation)


@router.get("", response_model=list[ConsultationResponse])
def list_consultations(status: str | None = None, db: Session = Depends(get_db)) -> list[ConsultationResponse]:
    return [_to_response(c) for c in service.list_consultations(db, status=status)]


@router.post("/{consultation_id}/decision", response_model=ConsultationResponse)
def decide_consultation(
    consultation_id: str, payload: ConsultationDecisionRequest, db: Session = Depends(get_db)
) -> ConsultationResponse:
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