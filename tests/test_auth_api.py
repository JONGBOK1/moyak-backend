"""앱 회원가입·로그인 API — Supabase Auth는 가짜로 대체, 실제 계정 생성·네트워크 호출 없음."""
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded
from sqlalchemy import create_engine, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src import config
from src.accounts import supabase_auth, users
from src.api.limiter import limiter, rate_limit_exceeded_handler
from src.api.routes import auth as routes
from src.api.routes import consultation, conversation
from src.consult.db import Base, get_db

PASSWORD = "moyak123!"


class FakeSupabase:
    """Supabase Auth 흉내: 이메일 → (id, 비밀번호)."""

    def __init__(self):
        self.accounts = {}
        self.deleted = []

    def create_user(self, email, password, username):
        if email in self.accounts:
            raise supabase_auth.AccountExists()
        self.accounts[email] = (str(uuid.uuid4()), password)
        return self.accounts[email][0]

    def sign_in(self, email, password):
        account = self.accounts.get(email)
        if not account or account[1] != password:
            raise supabase_auth.InvalidCredentials()
        return account[0]

    def delete_user(self, user_id):
        self.deleted.append(user_id)


@pytest.fixture
def setup(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    users.create_local_table(engine)
    factory = sessionmaker(bind=engine)
    fake = FakeSupabase()
    for name in ("create_user", "sign_in", "delete_user"):
        monkeypatch.setattr(supabase_auth, name, getattr(fake, name))
    monkeypatch.setattr(config, "CONSULT_AUTH_SECRET", "x" * 32)
    monkeypatch.setattr(limiter, "enabled", False)
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(routes.router)
    app.include_router(consultation.router)
    app.include_router(conversation.router)

    def dependency():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = dependency
    with TestClient(app) as client:
        yield client, factory, fake
    engine.dispose()


def signup(client, username="grandma_01", password=PASSWORD):
    return client.post("/auth/signup", json={"username": username, "password": password})


def test_signup_login_and_me(setup):
    client, factory, fake = setup
    assert client.get("/auth/username-available", params={"username": "grandma_01"}).json()["available"]
    created = signup(client)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["token_type"] == "bearer" and body["user"]["username"] == "grandma_01"
    assert body["user"]["role"] == "user"
    # Supabase에는 내부 이메일로, users에는 같은 UUID로 저장
    assert "grandma_01@moyak.app" in fake.accounts
    assert fake.accounts["grandma_01@moyak.app"][0] == body["user"]["id"]
    assert not client.get("/auth/username-available", params={"username": "Grandma_01"}).json()["available"]

    logged_in = client.post("/auth/login", json={"username": "GRANDMA_01", "password": PASSWORD})
    assert logged_in.status_code == 200, logged_in.text
    token = logged_in.json()["access_token"]
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json() == body["user"]
    with factory() as db:
        assert users.find_by_id(db, body["user"]["id"])["last_login_at"] is not None


def test_token_identifies_user_in_consultation_api(setup):
    client, _, _ = setup
    session = signup(client).json()
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    uid = session["user"]["id"]
    mine = client.post("/consultations", json={"user_id": uid}).json()["id"]
    other = client.post("/consultations", json={"user_id": "someone-else"}).json()["id"]
    # 토큰으로 본인 확인 통과(약사 배정 전이라 409), 남의 상담은 403
    assert client.get(f"/consultations/{mine}/session", headers=auth).status_code == 409
    assert client.get(f"/consultations/{other}/session", headers=auth).status_code == 403
    expired = {"Authorization": "Bearer " + session["access_token"][:-1] + "0"}
    assert client.get(f"/consultations/{mine}/session", headers=expired).status_code == 401


def test_signup_rejects_duplicates_and_invalid_input(setup):
    client, _, _ = setup
    assert signup(client).status_code == 201
    assert signup(client, username="GRANDMA_01").status_code == 409
    assert signup(client, username="ab").status_code == 422
    assert signup(client, username="한글아이디").status_code == 422
    for weak in ["short1!", "nodigits!!", "nospecial12", "has space1!"]:
        assert signup(client, username="newuser", password=weak).status_code == 422, weak
    assert client.get("/auth/username-available", params={"username": "x"}).status_code == 422


def test_login_failures(setup):
    client, factory, _ = setup
    uid = signup(client).json()["user"]["id"]
    for username, password in [("grandma_01", "wrong123!"), ("nobody", PASSWORD), ("!!", PASSWORD)]:
        response = client.post("/auth/login", json={"username": username, "password": password})
        assert response.status_code == 401
        assert response.json()["detail"] == "아이디 또는 비밀번호가 올바르지 않습니다."
    with factory() as db:
        db.execute(update(users.users).where(users.users.c.id == uid).values(is_active=False))
        db.commit()
    assert client.post("/auth/login", json={"username": "grandma_01", "password": PASSWORD}).status_code == 403
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer bad.token"}).status_code == 401


def test_login_creates_missing_profile(setup):
    """대시보드에서 Auth 계정만 만든 경우 첫 로그인 때 users 프로필을 채운다."""
    client, factory, fake = setup
    fake.accounts["pharma01@moyak.app"] = ("00000000-0000-4000-8000-0000000000aa", PASSWORD)
    response = client.post("/auth/login", json={"username": "pharma01", "password": PASSWORD})
    assert response.status_code == 200, response.text
    with factory() as db:
        assert users.find_by_id(db, "00000000-0000-4000-8000-0000000000aa")["nickname"] == "pharma01"


def test_pharmacist_role_comes_from_users_table(setup):
    client, factory, _ = setup
    uid = signup(client, username="pharm_kim").json()["user"]["id"]
    with factory() as db:
        db.execute(update(users.users).where(users.users.c.id == uid).values(role="pharmacist"))
        db.commit()
    session = client.post("/auth/login", json={"username": "pharm_kim", "password": PASSWORD}).json()
    assert session["user"]["role"] == "pharmacist"


def test_signup_rolls_back_auth_account_when_profile_save_fails(setup, monkeypatch):
    client, _, fake = setup

    def broken(*args):
        raise RuntimeError("db down")
    monkeypatch.setattr(users, "add", broken)
    with pytest.raises(RuntimeError):
        signup(client)
    assert fake.deleted == [fake.accounts["grandma_01@moyak.app"][0]]


def test_unconfigured_supabase_returns_503(setup, monkeypatch):
    client, _, _ = setup
    monkeypatch.setattr(config, "SUPABASE_URL", "")

    def unavailable(*args):
        supabase_auth._require("")
    monkeypatch.setattr(supabase_auth, "create_user", unavailable)
    assert signup(client).status_code == 503


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.ok = 200 <= status < 300
        self.text = str(body)

    def json(self):
        return self._body


def test_supabase_requests(monkeypatch):
    """관리자 API는 service_role 키, 비밀번호 로그인은 anon 키로 호출한다."""
    monkeypatch.setattr(config, "SUPABASE_URL", "https://proj.supabase.co")
    monkeypatch.setattr(config, "SUPABASE_ANON_KEY", "anon")
    monkeypatch.setattr(config, "SUPABASE_SERVICE_ROLE_KEY", "service")
    calls, replies = [], []

    def fake_request(method, url, headers, timeout, **kwargs):
        calls.append((method, url, headers["apikey"], kwargs.get("json")))
        return replies.pop(0)
    monkeypatch.setattr(supabase_auth.requests, "request", fake_request)

    replies.append(FakeResponse(200, {"id": "uid-1"}))
    assert supabase_auth.create_user("a@moyak.app", PASSWORD, "a") == "uid-1"
    method, url, key, body = calls[-1]
    assert (method, url, key) == ("POST", "https://proj.supabase.co/auth/v1/admin/users", "service")
    assert body["email_confirm"] is True and body["password"] == PASSWORD

    replies.append(FakeResponse(422, {"error_code": "email_exists", "msg": "already been registered"}))
    with pytest.raises(supabase_auth.AccountExists):
        supabase_auth.create_user("a@moyak.app", PASSWORD, "a")

    replies.append(FakeResponse(200, {"access_token": "t", "user": {"id": "uid-1"}}))
    assert supabase_auth.sign_in("a@moyak.app", PASSWORD) == "uid-1"
    assert calls[-1][1].endswith("/auth/v1/token?grant_type=password") and calls[-1][2] == "anon"

    replies.append(FakeResponse(400, {"error": "invalid_grant"}))
    with pytest.raises(supabase_auth.InvalidCredentials):
        supabase_auth.sign_in("a@moyak.app", "wrong")

    replies.append(FakeResponse(500, {}))
    with pytest.raises(supabase_auth.AuthUnavailable):
        supabase_auth.sign_in("a@moyak.app", PASSWORD)


def test_status_reports_configuration_without_values(setup, monkeypatch):
    client, _, _ = setup
    monkeypatch.setattr(config, "SUPABASE_URL", "")
    monkeypatch.setattr(config, "SUPABASE_ANON_KEY", "anon-secret-value")
    body = client.get("/auth/status").json()
    assert body["supabase_url"] is False and body["anon_key"] is True and body["token_secret"] is True
    assert body["supabase_auth_http"] is None
    assert "anon-secret-value" not in str(body)
