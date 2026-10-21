"""앱 회원가입·로그인 API (Supabase Auth).

로그인 응답의 `access_token`을 이후 요청에 `Authorization: Bearer <토큰>`으로 보내면,
상담·채팅·녹음 API가 그 사용자(토큰의 user.id·role)로 처리한다. 로그아웃은 앱에서 토큰을 지우면 된다(서버 저장 없음).
"""
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.accounts import service, supabase_auth, users
from src.api.limiter import limiter
from src.consult.auth import verify_token
from src.consult.db import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


class Credentials(BaseModel):
    username: str = Field(..., max_length=64)
    password: str = Field(..., max_length=128)


class SignupRequest(Credentials):
    name: str | None = Field(None, max_length=40, description="실명(선택). 마이페이지에 표시")


class NameUpdate(BaseModel):
    name: str = Field(..., max_length=40)


class UserProfile(BaseModel):
    id: str
    username: str | None
    name: str | None = None  # 실명(마이페이지 표시용). 입력 전이면 null
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
def signup(request: Request, payload: SignupRequest, db: Session = Depends(get_db)):
    """회원가입 완료 → 바로 로그인된 상태(토큰)를 돌려준다. name(실명)은 선택."""
    return service.signup(db, payload.username, payload.password, payload.name)


@router.post("/login", response_model=SessionResponse)
@limiter.limit("10/minute")
def login(request: Request, payload: Credentials, db: Session = Depends(get_db)):
    return service.login(db, payload.username, payload.password)


def _current_user(authorization: str, db: Session):
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "로그인이 필요합니다.")
    actor = verify_token(token)
    user = users.find_by_id(db, actor.id)
    if user is None or not user["is_active"] or user["deleted_at"] is not None:
        raise HTTPException(401, "로그인이 필요합니다.")
    return user


@router.get("/me", response_model=UserProfile)
def me(authorization: str = Header(default=""), db: Session = Depends(get_db)):
    """앱 시작 시 저장된 토큰이 아직 유효한지 확인 + 마이페이지 표시(실명 포함)."""
    return service.profile(_current_user(authorization, db))


@router.patch("/me", response_model=UserProfile)
@limiter.limit("20/minute")
def update_me(request: Request, payload: NameUpdate, authorization: str = Header(default=""),
              db: Session = Depends(get_db)):
    """마이페이지에서 실명 입력·수정. 본인 토큰으로만 바꿀 수 있다."""
    user = _current_user(authorization, db)
    return service.update_name(db, str(user["id"]), payload.name)


@router.get("/status")
def status():
    """배포 환경 점검용: 로그인 설정이 들어갔는지(true/false)와 Supabase Auth 응답 코드. 키 값은 보여주지 않는다."""
    return supabase_auth.health()
