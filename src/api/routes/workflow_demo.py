"""Local-only scenario identities. Mount only on src.api.demo, never production."""
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from src import config
from src.consult.auth import Actor, current_actor, issue_token, verify_token
from src.consult.db import get_db
from src.consult.models import ConsultationRequest, ConsultationSession
from src.consult import conversation, service
from src.api.routes.consultation import _to_response

router = APIRouter(prefix="/demo/workflow")
PHARMACIST = "demo-pharmacist-workflow"


@router.post('/pharmacist')
def pharmacist(request: Request):
    if request.headers.get('x-moyak-demo') != '1':
        raise HTTPException(403, '시연 화면에서 요청해주세요.')
    return {'id': PHARMACIST, 'token': issue_token(PHARMACIST, 'pharmacist')}


@router.post('/start')
def start(request: Request, db: Session = Depends(get_db)):
    if request.headers.get('x-moyak-demo') != '1':
        raise HTTPException(403, '시연 화면에서 요청해주세요.')
    authorization = request.headers.get('authorization', '')
    actor = verify_token(authorization.removeprefix('Bearer ')) if authorization else Actor('demo-user-'+uuid.uuid4().hex,'user')
    if actor.role != 'user' or not actor.id.startswith('demo-user-'):
        raise HTTPException(403, '시연 사용자만 요청할 수 있습니다.')
    row = db.query(ConsultationRequest).filter_by(user_id=actor.id, status='pending').order_by(ConsultationRequest.created_at.desc()).first()
    if row:
        room = db.get(ConsultationSession, row.id)
        if room and room.ended_at:
            row = None
    if not row:
        row = ConsultationRequest(user_id=actor.id, chat_summary=service.DIRECT_REQUEST_SUMMARY)
        db.add(row)
        db.commit()
    return {'id': row.id, 'workflow': True, 'user_id': actor.id,
            'user_token': issue_token(actor.id,'user'), 'pharmacist_id': PHARMACIST,
            'ai_available': bool(config.OPENAI_API_KEY), 'video_available': bool(config.DAILY_API_KEY)}


@router.get('/{cid}/state')
def state(cid: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    row = db.get(ConsultationRequest, cid)
    if not row or (actor.role == 'user' and row.user_id != actor.id) or (actor.role == 'pharmacist' and row.pharmacist_id != actor.id):
        raise HTTPException(403, '본인 상담만 조회할 수 있습니다.')
    room = db.get(ConsultationSession, cid)
    return {'consultation': _to_response(row),
            'session': conversation.session_view(room) if room else None}
