"""키오스크 QR 로그인 시 가린 실명 표시 (2026-10-19) — 이종복 → 이*복. API 호출 없음."""
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.accounts import users
from src.accounts.users import mask_name
from src.api.routes import vending
from src.consult.db import Base, get_db


def test_mask_name():
    assert mask_name("이종복") == "이*복"
    assert mask_name("김철") == "김*"
    assert mask_name("남궁민수") == "남**수"
    assert mask_name("이") == "이"
    assert mask_name(" 이 종 복 ") == "이*복"  # 공백 무시
    assert mask_name("") is None and mask_name(None) is None


def _client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    users.create_local_table(engine)
    factory = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(vending.router)

    def dependency():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = dependency
    return TestClient(app), factory


def _login(client, user_id):
    qr = client.post("/vending/machines/VM-001/rotate-qr").json()
    client.post("/vending/scan", json={"machine_id": "VM-001", "qr_token": qr["qr_token"], "user_id": user_id})
    return client.get("/vending/machines/VM-001/session").json()


def test_kiosk_session_shows_masked_real_name():
    client, factory = _client()
    uid = str(uuid.uuid4())
    with factory() as db:
        db.execute(insert(users.users).values(id=uid, email="jb@moyak.app", nickname="jongbok", name="이종복", role="user"))
        db.commit()
    session = _login(client, uid)
    assert session["paired"] and session["user_display_name"] == "이*복"


def test_kiosk_session_without_name_or_signup_falls_back():
    client, factory = _client()
    no_name = str(uuid.uuid4())
    with factory() as db:
        db.execute(insert(users.users).values(id=no_name, email="nn@moyak.app", nickname="noname", role="user"))
        db.commit()
    assert _login(client, no_name)["user_display_name"] is None  # 이름 입력 전 → "연동 회원 님"
    assert _login(client, "demo-web-user")["user_display_name"] is None  # 가입 안 한 시연용 ID
