"""Supabase Auth(GoTrue) REST 호출. 비밀번호 저장·검증은 전부 Supabase가 한다.

- 가입: service_role 키로 관리자 API를 호출해 이메일 확인 없이 계정을 만든다(가짜 이메일이라 확인 메일을 받을 수 없음).
- 로그인: anon 키로 비밀번호 로그인(grant_type=password)을 호출해 맞으면 사용자 UUID를 돌려받는다.
"""
import re

import requests

from src import config

TIMEOUT = 10


class AuthUnavailable(Exception):
    """Supabase 설정이 없거나 응답이 이상할 때 (503)."""


class AccountExists(Exception):
    """같은 아이디(이메일)가 이미 Supabase Auth에 있을 때 (409)."""


class InvalidCredentials(Exception):
    """아이디 또는 비밀번호가 틀렸을 때 (401)."""


def _require(*values: str) -> None:
    if not config.SUPABASE_URL or not all(values):
        raise AuthUnavailable("SUPABASE_URL·SUPABASE_ANON_KEY·SUPABASE_SERVICE_ROLE_KEY 설정이 필요합니다.")


def _headers(key: str) -> dict:
    return {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _call(method: str, path: str, key: str, **kwargs) -> requests.Response:
    try:
        return requests.request(method, config.SUPABASE_URL + path, headers=_headers(key), timeout=TIMEOUT, **kwargs)
    except requests.RequestException as error:
        raise AuthUnavailable(f"Supabase Auth에 연결하지 못했습니다 ({type(error).__name__})") from error


def _reason(response: requests.Response) -> str:
    """Supabase 오류 응답의 오류 코드만 꺼낸다(키·개인정보 없음)."""
    try:
        body = response.json()
        return str(body.get("error_code") or body.get("error") or body.get("code") or "")[:60]
    except ValueError:
        return ""


def health() -> dict:
    """설정 여부와 Supabase Auth 응답 코드만 보여주는 진단 (값은 노출하지 않음)."""
    result = {
        "supabase_url": bool(config.SUPABASE_URL),
        "anon_key": bool(config.SUPABASE_ANON_KEY),
        "service_role_key": bool(config.SUPABASE_SERVICE_ROLE_KEY),
        "token_secret": len(config.CONSULT_AUTH_SECRET) >= 32,
        # 형식만 확인: https://<프로젝트ID>.supabase.co 이어야 한다 (주소 자체는 보여주지 않음)
        "supabase_url_format_ok": bool(re.fullmatch(r"https://[a-z0-9]+\.supabase\.co", config.SUPABASE_URL)),
        "supabase_auth_http": None,
    }
    if config.SUPABASE_URL and config.SUPABASE_ANON_KEY:
        try:
            result["supabase_auth_http"] = _call("GET", "/auth/v1/health", config.SUPABASE_ANON_KEY).status_code
        except AuthUnavailable as error:
            result["supabase_auth_http"] = "unreachable: " + type(error.__cause__).__name__
    return result


def create_user(email: str, password: str, username: str) -> str:
    """새 계정을 만들고 Supabase 사용자 UUID를 반환한다."""
    _require(config.SUPABASE_SERVICE_ROLE_KEY)
    response = _call("POST", "/auth/v1/admin/users", config.SUPABASE_SERVICE_ROLE_KEY, json={
        "email": email, "password": password, "email_confirm": True,
        "user_metadata": {"username": username},
    })
    if response.status_code in (400, 422):
        body = response.text.lower()
        if "exist" in body or "registered" in body:
            raise AccountExists()
    if not response.ok:
        raise AuthUnavailable(f"Supabase 계정 생성 실패 (HTTP {response.status_code} {_reason(response)})")
    user_id = response.json().get("id")
    if not user_id:
        raise AuthUnavailable("Supabase 응답에 사용자 ID가 없습니다.")
    return user_id


def delete_user(user_id: str) -> None:
    """가입 도중 users 테이블 저장이 실패했을 때 방금 만든 Auth 계정을 되돌린다(최선 노력)."""
    if config.SUPABASE_URL and config.SUPABASE_SERVICE_ROLE_KEY:
        try:
            _call("DELETE", f"/auth/v1/admin/users/{user_id}", config.SUPABASE_SERVICE_ROLE_KEY)
        except AuthUnavailable:
            pass


def sign_in(email: str, password: str) -> str:
    """비밀번호가 맞으면 Supabase 사용자 UUID를 반환한다."""
    _require(config.SUPABASE_ANON_KEY)
    response = _call("POST", "/auth/v1/token?grant_type=password", config.SUPABASE_ANON_KEY,
                     json={"email": email, "password": password})
    if response.status_code in (400, 401):
        raise InvalidCredentials()
    if not response.ok:
        raise AuthUnavailable(f"Supabase 로그인 실패 (HTTP {response.status_code} {_reason(response)})")
    user_id = (response.json().get("user") or {}).get("id")
    if not user_id:
        raise AuthUnavailable("Supabase 응답에 사용자 ID가 없습니다.")
    return user_id
