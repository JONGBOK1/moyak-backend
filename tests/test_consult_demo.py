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


def test_workflow_consultation_to_kiosk(client):
    marker = {'X-Moyak-Demo': '1'}
    user = client.post('/demo/workflow/start', headers=marker).json()
    pharmacy = client.post('/demo/workflow/pharmacist', headers=marker).json()
    uh = {'Authorization': 'Bearer ' + user['user_token']}
    ph = {'Authorization': 'Bearer ' + pharmacy['token']}
    cid = user['id']
    path = '/consultations/' + cid
    assert client.get(f'/demo/workflow/{cid}/state', headers=uh).json()['session'] is None
    assert client.post(path + '/session/claim', headers=ph).status_code == 200
    assert client.post(path + '/cancel', headers=uh).status_code == 409
    assert client.post(path + '/messages', headers=uh,
                       json={'client_id': 'workflow-1', 'text': 'Test consultation'}).status_code == 201
    assert client.get(path + '/messages', headers=ph).json()[0]['text'] == 'Test consultation'
    decision = client.post(path + '/decision', headers=ph, json={
        'pharmacist_id': pharmacy['id'], 'approve': True, 'drug_item_seq': 'test-drug',
        'drug_item_name': 'Test medicine', 'price': 3000,
    })
    assert decision.status_code == 200, decision.text
    purchase = decision.json()['approved_purchase_id']
    assert client.post(path + '/session/end', headers=ph).status_code == 200
    # 키오스크 라우트는 팀 master 원본 그대로 (요청의 user_id를 신뢰, 별도 인증 없음)
    assert client.get('/vending/purchases', params={'user_id': user['user_id']}).json()[0]['price'] == 3000
    assert client.get('/vending/purchases', params={'user_id': 'someone-else'}).json() == []
    qr = client.post('/vending/machines/M001/rotate-qr').json()
    assert '<svg' in qr['qr_svg'] or ':svg' in qr['qr_svg']
    scan = {'machine_id': 'M001', 'qr_token': qr['qr_token'], 'user_id': user['user_id']}
    assert client.post('/vending/scan', json=scan).json()['has_pending_purchase']
    assert client.get('/vending/purchases', params={'machine_id': 'M001', 'user_id': user['user_id']}).status_code == 200
    dispense = {'machine_id': 'M001', 'purchase_id': purchase}
    assert client.post('/vending/dispense', json=dispense).status_code == 409
    payment = client.post('/vending/purchases/pay', json={'machine_id': 'M001', 'purchase_ids': [purchase, purchase]})
    assert payment.json()['total_amount'] == 3000
    assert not client.get('/vending/machines/M001/session').json()['paired']
    assert client.post('/vending/dispense', json=dispense).json()['status'] == 'dispensed'
    assert client.post('/vending/dispense', json=dispense).status_code == 409


def test_workflow_cancel_and_static_pages(client):
    user = client.post('/demo/workflow/start', headers={'X-Moyak-Demo': '1'}).json()
    uh = {'Authorization': 'Bearer ' + user['user_token']}
    other = client.post('/demo/workflow/start', headers={'X-Moyak-Demo': '1'}).json()
    oh = {'Authorization': 'Bearer ' + other['user_token']}
    path = '/consultations/' + user['id']
    assert client.post(path + '/cancel', headers=oh).status_code == 403
    assert client.get('/demo/workflow/' + user['id'] + '/state', headers=oh).status_code == 403
    assert client.post(path + '/cancel', headers=uh).json()['status'] == 'cancelled'
    restarted = client.post('/demo/workflow/start', headers={**uh, 'X-Moyak-Demo': '1'}).json()
    assert restarted['id'] != user['id']
    assert restarted['user_id'] == user['user_id']
    for url in ['/pharmacist', '/scan', '/kiosk/pairing/', '/kiosk/cart2/', '/static/vendor/jsQR.js']:
        assert client.get(url).status_code == 200


def test_workflow_shop_stock_and_payment(client, monkeypatch):
    monkeypatch.setenv('MOYAK_LOCAL_DEMO', '1')
    products = client.get('/vending/machines/M001/products').json()
    item = products[0]
    body = {'items': [{'product_id': item['id'], 'quantity': 2}]}
    order = client.post('/vending/machines/M001/orders', json=body).json()
    assert order['total_amount'] == item['price'] * 2
    path = '/vending/orders/' + order['id']
    assert client.post(path + '/dispense').status_code == 409
    assert client.post(path + '/pay').json()['status'] == 'paid'
    assert client.post(path + '/pay').status_code == 409
    assert client.post(path + '/dispense').json()['status'] == 'dispensed'
    assert client.post(path + '/dispense').status_code == 409
    updated = client.get('/vending/machines/M001/products').json()
    assert next(p for p in updated if p['id'] == item['id'])['stock'] == item['stock'] - 2


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
