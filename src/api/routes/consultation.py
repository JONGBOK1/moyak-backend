from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.api.limiter import limiter
from src.consult import service
from src.consult import conversation as conversation_service
from src.consult.auth import Actor, current_actor
from src.consult.db import get_db
from src.consult.models import ConsultationRequest, ConsultationSession

router = APIRouter(prefix="/consultations", tags=["consultation"])


class ConsultationCreateRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    chat_summary: str | None = Field(
        None, description="챗봇 상담 대화 요약 (약사가 참고). 챗봇 없이 바로 상담을 시작한 경우 비워도 됨"
    )
    requested_drug_item_seq: str | None = None
    requested_drug_name: str | None = None
    machine_code: str | None = Field(
        None, max_length=40, description="사용자가 앱에서 고른 키오스크 code(예: VM-001). 있으면 약사는 그 키오스크 재고에 있는 약만 승인 가능"
    )


class PrescriptionItem(BaseModel):
    drug_item_seq: str = Field(..., min_length=1)
    drug_item_name: str = Field(..., min_length=1)
    quantity: int = Field(1, ge=1, le=10)
    price: int | None = Field(None, ge=0)  # 이 약 수량 전체 금액. 키오스크 지정 상담에서 비우면 판매가 × 수량


class ApprovedItem(BaseModel):
    purchase_id: str
    drug_item_seq: str
    drug_item_name: str
    quantity: int
    price: int | None = None


class ConsultationDecisionRequest(BaseModel):
    pharmacist_id: str = Field(..., min_length=1)
    approve: bool
    reason: str | None = None
    drug_item_seq: str | None = None
    drug_item_name: str | None = None
    price: int | None = Field(None, ge=0)  # 결제 금액(원, 수량 전체 총액). 키오스크 지정 상담에서 비우면 판매가 × 수량
    quantity: int = Field(1, ge=1, le=10)  # 처방 수량
    # 여러 종류 처방(2026-10-23): 있으면 위의 drug_item_*/price/quantity 대신 이 목록을 쓴다
    items: list["PrescriptionItem"] | None = Field(None, min_length=1, max_length=5)


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
    machine_code: str | None = None  # 사용자가 고른 키오스크
    approved_quantity: int | None = None  # 승인된 수량 (약이 한 종류일 때만, 여러 종류면 null — approved_items 참고)
    approved_items: list[ApprovedItem] = []  # 처방된 약 전체(약 하나당 승인 건 1개)

    class Config:
        from_attributes = True


class MessageCreateRequest(BaseModel):
    client_id: str | None = Field(None, min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')
    sender_role: str = Field(..., pattern="^(user|pharmacist)$")
    sender_id: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1, max_length=1000)


class MessageResponse(BaseModel):
    id: int
    client_id: str
    sender_role: str
    sender_id: str
    content: str
    created_at: datetime

    class Config:
        from_attributes = True


def _to_response(consultation) -> ConsultationResponse:
    response = ConsultationResponse.model_validate(consultation)
    purchases = consultation.purchases
    if purchases:
        response.approved_purchase_id = purchases[0].id
        response.approved_items = [
            ApprovedItem(purchase_id=p.id, drug_item_seq=p.drug_item_seq, drug_item_name=p.drug_item_name,
                         quantity=p.quantity or 1, price=p.price) for p in purchases]
        if len(purchases) == 1:  # 예전 화면 호환: 이름 + 수량 따로
            response.approved_drug_name = purchases[0].drug_item_name
            response.approved_quantity = purchases[0].quantity or 1
        else:  # 여러 종류: 이름에 수량까지 붙여 한 줄로("A × 2개, B")
            response.approved_drug_name = consultation.drug_summary
    return response


@router.post("", response_model=ConsultationResponse)
def create_consultation(payload: ConsultationCreateRequest, db: Session = Depends(get_db)) -> ConsultationResponse:
    try:
        consultation = service.create_consultation(
            db,
            user_id=payload.user_id,
            chat_summary=payload.chat_summary,
            requested_drug_item_seq=payload.requested_drug_item_seq,
            requested_drug_name=payload.requested_drug_name,
            machine_code=payload.machine_code,
        )
    except service.NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
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
            quantity=payload.quantity,
            items=[i.model_dump() for i in payload.items] if payload.items else None,
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
            client_id=payload.client_id,
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


class PresenceResponse(BaseModel):
    available: bool  # 화상방이 없거나 Daily 조회 실패 시 False (화면은 알림만 생략)
    participants: int  # 지금 화상방 접속 인원
    pharmacist_in_room: bool


@router.get("/{consultation_id}/presence", response_model=PresenceResponse)
def room_presence(consultation_id: str, db: Session = Depends(get_db)) -> PresenceResponse:
    """Daily 토큰에 연결된 담당 약사의 실제 입장을 확인한다. 인원수로 추정하지 않는다."""
    try:
        consultation = service.get_consultation(db, consultation_id)
    except service.NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    presence = service.video.pharmacist_presence(consultation.room_url, consultation.pharmacist_id)
    if presence is None:
        return PresenceResponse(available=False, participants=0, pharmacist_in_room=False)
    count, pharmacist = presence
    return PresenceResponse(available=True, participants=count, pharmacist_in_room=pharmacist)


@router.post('/{consultation_id}/pharmacist-token')
def pharmacist_join(consultation_id: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    if actor.role != 'pharmacist': raise HTTPException(403, '약사만 입장할 수 있습니다.')
    row = db.get(ConsultationRequest, consultation_id)
    if not row: raise HTTPException(404, '상담을 찾을 수 없습니다.')
    if row.status == 'cancelled' or row.ended_at or (row.pharmacist_id and row.pharmacist_id != actor.id):
        raise HTTPException(409, '입장할 수 없는 상담입니다.')
    session = db.get(ConsultationSession, consultation_id)
    if session and session.ended_at:
        raise HTTPException(409, '종료된 상담입니다.')
    token = service.video.pharmacist_token(row.room_url, actor.id)
    if not token: raise HTTPException(503, '화상 입장을 준비하지 못했습니다. 다시 시도해주세요.')
    conversation_service.assign(db, consultation_id, actor)
    return {'room_url': row.room_url, 'meeting_token': token}
