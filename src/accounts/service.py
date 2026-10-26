"""회원가입·로그인 흐름. 앱 화면 규칙(utils/input_validation.dart)과 같은 검사를 서버에서도 한다."""
import logging
import re

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src import config
from src.accounts import supabase_auth, users
from src.consult.auth import issue_token

logger = logging.getLogger(__name__)

USERNAME = re.compile(r"^[A-Za-z0-9_]{4,20}$")


def normalize_username(username: str) -> str:
    username = (username or "").strip()
    if not USERNAME.fullmatch(username):
        raise HTTPException(422, "아이디는 영문, 숫자, 밑줄로 4~20자 입력해주세요.")
    return username.lower()  # 대소문자만 다른 아이디는 같은 아이디로 본다


def check_password(password: str) -> None:
    ok = (len(password) >= 8 and not re.search(r"\s", password) and re.search(r"[A-Za-z]", password)
          and re.search(r"[0-9]", password) and re.search(r"[^A-Za-z0-9\s]", password))
    if not ok:
        raise HTTPException(422, "비밀번호는 영문, 숫자, 특수문자를 포함해 공백 없이 8자 이상 입력해주세요.")


NAME = re.compile(r"^[가-힣A-Za-z][가-힣A-Za-z .·]{0,39}$")


def normalize_name(name: str | None) -> str | None:
    """실명: 한글·영문(공백·점 허용) 1~40자. 비우면 None(선택 항목)."""
    name = " ".join((name or "").split())
    if not name:
        return None
    if not NAME.fullmatch(name):
        raise HTTPException(422, "이름은 한글 또는 영문으로 40자 이내로 입력해주세요.")
    return name


def email_for(username: str) -> str:
    return f"{username}@{config.AUTH_EMAIL_DOMAIN}"


def _unavailable(error: Exception) -> HTTPException:
    # 원인(설정 누락·연결 실패·Supabase HTTP 상태)만 남기고 키 값은 남기지 않는다 — Render Logs에서 확인
    logger.warning("Supabase Auth unavailable: %s", error)
    return HTTPException(503, "로그인 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해주세요.")


def _session(user) -> dict:
    ttl = config.AUTH_TOKEN_TTL_SECONDS
    return {
        "access_token": issue_token(str(user["id"]), user["role"], ttl=ttl),
        "token_type": "bearer",
        "expires_in": ttl,
        "user": profile(user),
    }


def profile(user) -> dict:
    return {"id": str(user["id"]), "username": user["nickname"], "name": user.get("name"), "role": user["role"]}


def username_available(db: Session, username: str) -> bool:
    return users.find_by_email(db, email_for(normalize_username(username))) is None


def signup(db: Session, username: str, password: str, name: str | None = None) -> dict:
    username = normalize_username(username)
    check_password(password)
    # 이름 항목을 아예 안 보내면(예전 앱) 선택 항목. 보냈는데 비어 있으면 거절 — 새 Flutter 가입은 이름 필수(한별, 10/25)
    if name is not None and not name.strip():
        raise HTTPException(422, "이름을 입력해주세요.")
    name = normalize_name(name)
    email = email_for(username)
    if users.find_by_email(db, email) is not None:
        raise HTTPException(409, "이미 사용 중인 아이디입니다.")
    try:
        user_id = supabase_auth.create_user(email, password, username)
    except supabase_auth.AccountExists:
        raise HTTPException(409, "이미 사용 중인 아이디입니다.") from None
    except supabase_auth.AuthUnavailable as error:
        raise _unavailable(error) from error
    try:
        users.add(db, user_id, email, username, name)
        db.commit()
    except Exception as error:
        db.rollback()
        supabase_auth.delete_user(user_id)  # Auth 계정만 남는 반쪽 가입 방지
        if isinstance(error, IntegrityError):
            raise HTTPException(409, "이미 사용 중인 아이디입니다.") from None
        raise
    return _session(users.find_by_id(db, user_id))


def login(db: Session, username: str, password: str) -> dict:
    invalid = HTTPException(401, "아이디 또는 비밀번호가 올바르지 않습니다.")
    try:
        username = normalize_username(username)
    except HTTPException:
        raise invalid from None
    email = email_for(username)
    try:
        user_id = supabase_auth.sign_in(email, password)
    except supabase_auth.InvalidCredentials:
        raise invalid from None
    except supabase_auth.AuthUnavailable as error:
        raise _unavailable(error) from error
    user = users.find_by_id(db, user_id)
    if user is None:
        # Auth에는 있는데 프로필이 없는 경우(대시보드에서 직접 만든 계정 등) 프로필을 채운다.
        users.add(db, user_id, email, username)
    elif not user["is_active"] or user["deleted_at"] is not None:
        raise HTTPException(403, "사용할 수 없는 계정입니다. 관리자에게 문의해주세요.")
    users.mark_login(db, user_id)
    db.commit()
    return _session(users.find_by_id(db, user_id))


def update_name(db: Session, user_id: str, name: str | None) -> dict:
    """마이페이지에서 실명 입력·수정 (가입 때 이름을 안 받은 기존 계정용)."""
    name = normalize_name(name)
    if name is None:
        raise HTTPException(422, "이름을 입력해주세요.")
    users.set_name(db, user_id, name)
    db.commit()
    return profile(users.find_by_id(db, user_id))
