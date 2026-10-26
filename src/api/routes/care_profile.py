from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from src.accounts import care_profile, users
from src.consult.auth import Actor, current_actor, verify_token
from src.consult.db import get_db
from src.consult.models import ConsultationRequest

router = APIRouter(tags=['care-profile'])


def account(authorization: str = Header(default=''), db: Session = Depends(get_db)):
    scheme, _, token = authorization.partition(' ')
    if scheme.lower() != 'bearer' or not token:
        raise HTTPException(401, '로그인이 필요합니다.')
    actor = verify_token(token)
    row = users.find_by_id(db, actor.id)
    if not row or not row['is_active'] or row['deleted_at'] is not None:
        raise HTTPException(401, '로그인이 필요합니다.')
    return actor


@router.get('/auth/me/care-profile')
def get_profile(actor: Actor = Depends(account), db: Session = Depends(get_db)):
    return care_profile.read(db, actor.id)


@router.patch('/auth/me/care-profile')
def patch_profile(payload: care_profile.CareInput, actor: Actor = Depends(account), db: Session = Depends(get_db)):
    return care_profile.save(db, actor.id, payload)


@router.get('/consultations/{cid}/care-profile')
def consultation_profile(cid: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    # Retains the existing pharmacist demo identity contract, scoped to a consultation.
    row = db.get(ConsultationRequest, cid)
    if not row: raise HTTPException(404, '상담을 찾을 수 없습니다.')
    if actor.role != 'pharmacist' or (row.pharmacist_id and row.pharmacist_id != actor.id):
        raise HTTPException(403, '담당 약사만 확인할 수 있습니다.')
    return care_profile.read(db, row.user_id)
