from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src import config
from src.api.routes import consultation
from src.consult.auth import issue_token
from src.consult.db import Base, get_db
from src.consult import video


def test_create_consultation_does_not_require_gps_or_machine_configuration(monkeypatch):
    monkeypatch.setattr(config, "CONSULT_AUTH_SECRET", "test-secret-" * 4)
    monkeypatch.setattr(video, "create_room", lambda: "https://example.daily.co/test")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(consultation.router)
    def dependency():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = dependency
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {issue_token('user1', 'user')}"}
        result = client.post("/consultations", json={"user_id": "user1"}, headers=headers)
        assert result.status_code == 200
        body = result.json()
        assert body["status"] == "pending"
        assert body["room_url"] == "https://example.daily.co/test"
        assert client.get(f"/consultations/{body['id']}", headers=headers).json()["id"] == body["id"]
    engine.dispose()
