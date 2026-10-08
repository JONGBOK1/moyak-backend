"""Small signed session-token adapter; issue only after trusted login/role verification."""
import base64
import hashlib
import hmac
import json
import time
import os
from dataclasses import dataclass

from fastapi import Header, HTTPException, Request

from src import config


@dataclass(frozen=True)
class Actor:
    id: str
    role: str


def _secret() -> bytes:
    if len(config.CONSULT_AUTH_SECRET) < 32:
        raise HTTPException(503, "상담 인증 설정이 필요합니다.")
    return config.CONSULT_AUTH_SECRET.encode()


def issue_token(subject: str, role: str, ttl: int = 3600) -> str:
    """Internal integration point, never expose as an unauthenticated HTTP endpoint."""
    if not subject or role not in {"user", "pharmacist"} or not 1 <= ttl <= 86400:
        raise ValueError("Invalid token claims")
    payload = base64.urlsafe_b64encode(json.dumps({
        "sub": subject, "role": role, "exp": int(time.time()) + ttl,
        "aud": "moyak-consult",
    }).encode()).decode().rstrip("=")
    signature = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def verify_token(token: str) -> Actor:
    secret = _secret()
    try:
        if len(token) > 4096:
            raise ValueError()
        payload, signature = token.split(".")
        expected = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if (claims["aud"] != "moyak-consult" or claims["exp"] <= time.time()
                or claims["role"] not in {"user", "pharmacist"}
                or not isinstance(claims["sub"], str) or not claims["sub"]):
            raise ValueError()
        return Actor(claims["sub"], claims["role"])
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise HTTPException(401, "유효한 상담 인증 토큰이 필요합니다.") from None


def local_identity_allowed(connection) -> bool:
    """MOYAK_LOCAL_IDENTITY=1 로 켜는 '이 PC 전용' 시연 경계 (main.py 미들웨어에서만 사용)."""
    return (os.getenv('MOYAK_LOCAL_IDENTITY') == '1'
            and config.DATABASE_URL.startswith('sqlite:')
            and connection.client is not None
            and connection.client.host in {'127.0.0.1', '::1'})


def claimed_actor(subject, role) -> Actor:
    """팀 방식: 요청이 보낸 사용자 ID·역할을 그대로 신뢰한다 (실제 로그인 연동 전 임시).
    누구나 다른 ID·약사 역할을 주장할 수 있으므로, 로그인이 붙으면 Bearer 토큰만 받도록 교체할 것."""
    if not isinstance(subject, str) or not 1 <= len(subject) <= 128 or role not in {'user', 'pharmacist'}:
        raise HTTPException(401, '사용자 ID와 역할(user/pharmacist)이 필요합니다.')
    return Actor(subject, role)


# 기존 호출부 호환용 이름
def local_actor(connection, subject, role) -> Actor:
    return claimed_actor(subject, role)


def optional_actor(request: Request, authorization: str = Header(default="")) -> Actor | None:
    """Bearer 토큰 → X-Moyak-User-Id/X-Moyak-Role 헤더 순으로 확인, 둘 다 없으면 None."""
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer":
            raise HTTPException(401, "Bearer 인증이 필요합니다.")
        return verify_token(token)
    subject, role = request.headers.get('x-moyak-user-id'), request.headers.get('x-moyak-role')
    if subject is None and role is None:
        return None
    return claimed_actor(subject, role)


def current_actor(request: Request, authorization: str = Header(default="")) -> Actor:
    actor = optional_actor(request, authorization)
    if actor is None:
        raise HTTPException(401, 'X-Moyak-User-Id, X-Moyak-Role 헤더가 필요합니다.')
    return actor


# 팀 API 규격 엔드포인트용: 신원 헤더가 없으면 None(팀과 동일하게 검사 생략), 있으면 권한 검사
legacy_actor = optional_actor
