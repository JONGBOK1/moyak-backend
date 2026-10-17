"""Supabase Auth(GoTrue) REST 호출. 비밀번호 저장·검증은 전부 Supabase가 한다.

- 가입: service_role 키로 관리자 API를 호출해 이메일 확인 없이 계정을 만든다(가짜 이메일이라 확인 메일을 받을 수 없음).
- 로그인: anon 키로 비밀번호 로그인(grant_type=password)을 호출해 맞으면 사용자 UUID를 돌려받는다.
"""
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
        raise AuthUnavailable("Supabase Auth에 연결하지 못했습니다.") from error


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
        raise AuthUnavailable(f"Supabase 계정 생성 실패 (HTTP {response.status_code})")
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
        raise AuthUnavailable(f"Supabase 로그인 실패 (HTTP {response.status_code})")
    user_id = (response.json().get("user") or {}).get("id")
    if not user_id:
        raise AuthUnavailable("Supabase 응답에 사용자 ID가 없습니다.")
    return user_id
