"""Small signed session-token adapter; issue only after trusted login/role verification."""
import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from fastapi import Header, HTTPException

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


def current_actor(authorization: str = Header(default="")) -> Actor:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        raise HTTPException(401, "Bearer 인증이 필요합니다.")
    return verify_token(token)


def legacy_actor(authorization: str = Header(default="")) -> Actor | None:
    # Preserve existing demos only when the new feature has not been configured.
    return current_actor(authorization) if config.CONSULT_AUTH_SECRET else None
