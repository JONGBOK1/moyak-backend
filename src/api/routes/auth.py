"""앱 회원가입·로그인 API (Supabase Auth).

로그인 응답의 `access_token`을 이후 요청에 `Authorization: Bearer <토큰>`으로 보내면,
상담·채팅·녹음 API가 그 사용자(토큰의 user.id·role)로 처리한다. 로그아웃은 앱에서 토큰을 지우면 된다(서버 저장 없음).
"""
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.accounts import service, users
from src.api.limiter import limiter
from src.consult.auth import verify_token
from src.consult.db import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


class Credentials(BaseModel):
    username: str = Field(..., max_length=64)
    password: str = Field(..., max_length=128)


class UserProfile(BaseModel):
    id: str
    username: str | None
    role: str


class SessionResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int
    user: UserProfile


class AvailabilityResponse(BaseModel):
    username: str
    available: bool


@router.get("/username-available", response_model=AvailabilityResponse)
@limiter.limit("30/minute")
def username_available(request: Request, username: str = Query(..., max_length=64),
                       db: Session = Depends(get_db)) -> AvailabilityResponse:
    """회원가입 1단계 '중복 확인' 버튼."""
    return AvailabilityResponse(username=username.strip(), available=service.username_available(db, username))


@router.post("/signup", response_model=SessionResponse, status_code=201)
@limiter.limit("5/minute;30/day")
def signup(request: Request, payload: Credentials, db: Session = Depends(get_db)):
    """회원가입 완료 → 바로 로그인된 상태(토큰)를 돌려준다."""
    return service.signup(db, payload.username, payload.password)


@router.post("/login", response_model=SessionResponse)
@limiter.limit("10/minute")
def login(request: Request, payload: Credentials, db: Session = Depends(get_db)):
    return service.login(db, payload.username, payload.password)


@router.get("/me", response_model=UserProfile)
def me(authorization: str = Header(default=""), db: Session = Depends(get_db)):
    """앱 시작 시 저장된 토큰이 아직 유효한지 확인하는 용도."""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "로그인이 필요합니다.")
    actor = verify_token(token)
    user = users.find_by_id(db, actor.id)
    if user is None or not user["is_active"] or user["deleted_at"] is not None:
        raise HTTPException(401, "로그인이 필요합니다.")
    return service.profile(user)
