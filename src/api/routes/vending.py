import json
from datetime import datetime

import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.consult import service
from src.consult.db import get_db

router = APIRouter(prefix="/vending", tags=["vending"])


def _qr_svg(payload: dict) -> str:
    """QR을 서버에서 SVG로 직접 생성한다 — 자판기 화면이 외부 CDN(JS 라이브러리)에
    의존하지 않고도 항상 뜨게 하기 위함 (네트워크 환경에 따라 CDN이 막힐 수 있음)."""
    img = qrcode.make(json.dumps(payload), image_factory=qrcode.image.svg.SvgPathImage)
    return img.to_string(encoding="unicode")


class QrTokenResponse(BaseModel):
    machine_id: str
    qr_token: str
    qr_token_expires_at: datetime
    qr_svg: str


class ScanRequest(BaseModel):
    machine_id: str = Field(..., min_length=1)
    qr_token: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1)


class PurchaseResponse(BaseModel):
    id: str
    user_id: str
    consultation_id: str
    drug_item_seq: str
    drug_item_name: str
    approved_by: str
    status: str
    created_at: datetime
    expires_at: datetime
    dispensed_machine_id: str | None
    dispensed_at: datetime | None

    class Config:
        from_attributes = True


class ScanResponse(BaseModel):
    has_pending_purchase: bool
    purchase: PurchaseResponse | None = None


class SessionResponse(BaseModel):
    machine_id: str
    paired: bool
    purchase: PurchaseResponse | None = None


class DispenseRequest(BaseModel):
    purchase_id: str = Field(..., min_length=1)
    machine_id: str = Field(..., min_length=1)


@router.post("/machines/{machine_id}/rotate-qr", response_model=QrTokenResponse)
def rotate_qr(machine_id: str, name: str | None = None, db: Session = Depends(get_db)) -> QrTokenResponse:
    """자판기가 주기적으로 호출 — 새 QR 토큰을 발급받아 화면에 QR로 표시한다."""
    machine = service.rotate_qr_token(db, machine_id=machine_id, machine_name=name)
    svg = _qr_svg({"machine_id": machine.id, "qr_token": machine.qr_token})
    return QrTokenResponse(
        machine_id=machine.id,
        qr_token=machine.qr_token,
        qr_token_expires_at=machine.qr_token_expires_at,
        qr_svg=svg,
    )


@router.get("/machines/{machine_id}/session", response_model=SessionResponse)
def get_session(machine_id: str, db: Session = Depends(get_db)) -> SessionResponse:
    """자판기 화면이 폴링 — 지금 QR로 누군가 로그인(스캔)했는지 확인한다."""
    try:
        machine, purchase = service.get_machine_session(db, machine_id=machine_id)
    except service.NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return SessionResponse(machine_id=machine.id, paired=purchase is not None, purchase=purchase)


@router.post("/scan", response_model=ScanResponse)
def scan(payload: ScanRequest, db: Session = Depends(get_db)) -> ScanResponse:
    """앱이 QR을 스캔한 뒤 호출 — 이 사용자의 대기 중인 승인 건이 있는지 확인한다."""
    try:
        purchase = service.scan_qr(db, machine_id=payload.machine_id, qr_token=payload.qr_token, user_id=payload.user_id)
    except service.InvalidStateError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ScanResponse(has_pending_purchase=purchase is not None, purchase=purchase)


@router.post("/dispense", response_model=PurchaseResponse)
def dispense(payload: DispenseRequest, db: Session = Depends(get_db)) -> PurchaseResponse:
    """사용자가 앱에서 수령을 최종 확정 — 자판기 개방(모의) + 기록."""
    try:
        return service.dispense(db, purchase_id=payload.purchase_id, machine_id=payload.machine_id)
    except service.NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidStateError as e:
        raise HTTPException(status_code=409, detail=str(e))