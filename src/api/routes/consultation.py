from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.api.limiter import limiter
from src.consult import service
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
    price: int | None = Field(None, ge=0)  # 약사가 안내하는 판매가(원), 키오스크 결제 금액


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
    summary: str | None = None  # 화상 상담 종료 시 생성된 대화 요약
    ended_at: datetime | None = None

    class Config:
        from_attributes = True


class MessageCreateRequest(BaseModel):
    sender_role: str = Field(..., pattern="^(user|pharmacist)$")
    sender_id: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1, max_length=1000)


class MessageResponse(BaseModel):
    id: str
    sender_role: str
    sender_id: str
    content: str
    created_at: datetime

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
def list_consultations(
    status: str | None = None,
    user_id: str | None = None,
    pharmacist_id: str | None = None,
    db: Session = Depends(get_db),
) -> list[ConsultationResponse]:
    return [
        _to_response(c)
        for c in service.list_consultations(db, status=status, user_id=user_id, pharmacist_id=pharmacist_id)
    ]


@router.get("/{consultation_id}", response_model=ConsultationResponse)
def get_consultation(consultation_id: str, db: Session = Depends(get_db)) -> ConsultationResponse:
    """채팅 화면이 자신이 요청한 상담의 상태를 폴링할 때 쓰는 단건 조회."""
    try:
        consultation = service.get_consultation(db, consultation_id)
    except service.NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _to_response(consultation)


@router.post("/{consultation_id}/cancel", response_model=ConsultationResponse)
def cancel_consultation(consultation_id: str, db: Session = Depends(get_db)) -> ConsultationResponse:
    """사용자가 대기 화면에서 직접 상담 요청을 취소할 때 쓴다."""
    try:
        consultation = service.cancel_consultation(db, consultation_id=consultation_id)
    except service.NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidStateError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _to_response(consultation)


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
            price=payload.price,
        )
    except service.NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidStateError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _to_response(consultation)

@router.get("/{consultation_id}/messages", response_model=list[MessageResponse])
def list_messages(consultation_id: str, db: Session = Depends(get_db)) -> list[MessageResponse]:
    """화상 상담 채팅 목록 — 사용자 앱/약사 콘솔이 3초 간격으로 폴링한다."""
    try:
        return service.list_messages(db, consultation_id)
    except service.NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{consultation_id}/messages", response_model=MessageResponse)
@limiter.limit("60/minute")
def send_message(
    request: Request, consultation_id: str, payload: MessageCreateRequest, db: Session = Depends(get_db)
) -> MessageResponse:
    try:
        return service.add_message(
            db,
            consultation_id=consultation_id,
            sender_role=payload.sender_role,
            sender_id=payload.sender_id,
            content=payload.content,
        )
    except service.NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidStateError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/{consultation_id}/end", response_model=ConsultationResponse)
@limiter.limit("10/minute;100/day")
def end_consultation(request: Request, consultation_id: str, db: Session = Depends(get_db)) -> ConsultationResponse:
    """화상 상담 종료 → 대화 요약 생성(GPT-4o-mini, 실제 비용 발생이라 요청량 제한).
    사용자/약사 누가 먼저 눌러도 되고, 이미 종료된 상담이면 기존 요약을 그대로 반환한다."""
    try:
        consultation = service.end_consultation(db, consultation_id, llm=request.app.state.rewrite_llm)
    except service.NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidStateError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _to_response(consultation)
