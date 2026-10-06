import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src import config
from src.api import demo
from src.consult.auth import verify_token
from src.consult.db import Base, get_db


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "CONSULT_AUTH_SECRET", "demo-secret-" * 4)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(demo, "SessionLocal", factory)
    def dependency():
        with factory() as db:
            yield db
    demo.app.dependency_overrides[get_db] = dependency
    # Deliberately don't enter lifespan: tests must not run real AI jobs.
    client = TestClient(demo.app, base_url="http://127.0.0.1:8001", client=("127.0.0.1", 12345))
    yield client
    client.close()
    demo.app.dependency_overrides.clear()
    engine.dispose()


def test_demo_start_and_real_chat_flow(client):
    page = client.get("/")
    assert page.status_code == 200
    assert page.headers['X-Frame-Options'] == 'SAMEORIGIN'
    assert page.headers['Content-Security-Policy'] == "frame-ancestors 'self'"
    assert client.get("/demo-ui.js").status_code == 200
    response = client.post("/demo/start", headers={"X-Moyak-Demo": "1"})
    assert response.status_code == 200
    session = response.json()
    assert verify_token(session["user_token"]).role == "user"
    assert verify_token(session["pharmacist_token"]).role == "pharmacist"
    path = f"/consultations/{session['id']}/messages"
    headers = {"Authorization": "Bearer " + session["user_token"]}
    assert client.post(path, headers=headers, json={"client_id": "m1", "text": "시연 메시지"}).status_code == 201
    headers = {"Authorization": "Bearer " + session["pharmacist_token"]}
    assert client.get(path, headers=headers).json()[0]["text"] == "시연 메시지"


def test_demo_rejects_cross_origin_and_missing_marker(client):
    assert client.post("/demo/start").status_code == 403
    assert client.post("/demo/start", headers={"X-Moyak-Demo": "1", "Origin": "https://evil.example"}).status_code == 403
    assert client.post("/demo/start", headers={"X-Moyak-Demo": "1", "Host": "evil.example"}).status_code == 400


def test_demo_rejects_remote_clients():
    client = TestClient(demo.app, base_url="http://127.0.0.1:8001", client=("192.168.1.5", 12345))
    assert client.post("/demo/start", headers={"X-Moyak-Demo": "1"}).status_code == 403
    client.close()


def test_signaling_relays_only_to_same_consultation(client):
    room = client.post('/demo/start', headers={'X-Moyak-Demo': '1'}).json()
    other = client.post('/demo/start', headers={'X-Moyak-Demo': '1'}).json()
    path = f"ws://127.0.0.1:8001/demo/{room['id']}/signal"
    with client.websocket_connect(path) as wrong:
        wrong.send_json({'token': other['user_token']})
        assert wrong.receive()['code'] == 1008
    with client.websocket_connect(path) as user:
        user.send_json({'token': room['user_token']})
        assert user.receive_json()['type'] == 'waiting'
        with client.websocket_connect(path) as pharmacist:
            pharmacist.send_json({'token': room['pharmacist_token']})
            assert user.receive_json()['type'] == 'peer-ready'
            assert pharmacist.receive_json()['type'] == 'peer-ready'
            offer = {'type': 'offer', 'description': {'type': 'offer', 'sdp': 'test'}}
            user.send_json(offer)
            assert pharmacist.receive_json() == offer
            with client.websocket_connect(path) as duplicate:
                duplicate.send_json({'token': room['user_token']})
                assert duplicate.receive()['code'] == 1008
        assert user.receive_json()['type'] == 'peer-left'
