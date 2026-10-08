import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from src import config
from src.api import main
from src.api.routes import conversation as routes
from src.api.routes import map as maps
from src.consult import worker, ai, video
from src.consult.db import Base, get_db, schema_problems
from src.consult.models import Product, VendingMachine, VendingInventory


@pytest.fixture
def integrated(tmp_path, monkeypatch):
    url = 'sqlite:///' + str(tmp_path / 'test.db')
    engine = create_engine(url, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(config, 'DATABASE_URL', url)
    monkeypatch.setenv('MOYAK_LOCAL_IDENTITY', '1')
    monkeypatch.setenv('MOYAK_LOCAL_DEMO', '1')
    monkeypatch.setattr(routes, 'SessionLocal', factory)
    monkeypatch.setattr(video, 'create_room', lambda: 'https://test.daily.co/test')
    monkeypatch.setattr(video, 'room_participant_count', lambda url: 1)
    def dependency():
        with factory() as db:
            yield db
    main.app.dependency_overrides[get_db] = dependency
    main.app.dependency_overrides[maps.map_engine] = lambda: engine
    client = TestClient(main.app, base_url='http://127.0.0.1:8000', client=('127.0.0.1', 1234))
    yield client, factory, engine
    client.close()
    main.app.dependency_overrides.clear()
    engine.dispose()


def identity(role, subject):
    return {'X-Moyak-User-Id': subject, 'X-Moyak-Role': role}


def test_patient_presence_owner_expiry_and_assignment(integrated, monkeypatch):
    from src.consult import patient_presence
    client, _, _ = integrated
    now = [100.0]
    monkeypatch.setattr(patient_presence, 'monotonic', lambda: now[0])
    monkeypatch.setattr(patient_presence, '_seen', {})
    user, pharma = identity('user', 'presence-user'), identity('pharmacist', 'presence-pharma')
    cid = client.post('/consultations', headers=user, json={'user_id': 'presence-user'}).json()['id']
    path = '/consultations/' + cid
    assert client.get(path, headers=user).json()['patient_queue_state'] == 'offline'
    assert client.post(path+'/heartbeat', headers=pharma).status_code == 403
    assert client.post(path+'/heartbeat', headers=identity('user', 'other')).status_code == 403
    assert client.post(path+'/heartbeat', headers=user).status_code == 200
    assert client.get(path, headers=user).json()['patient_queue_state'] == 'waiting'
    assert client.post(path+'/session/claim', headers=pharma).status_code == 200
    assert client.get(path, headers=user).json()['patient_queue_state'] == 'assigned'
    now[0] += 61
    assert client.get(path, headers=user).json()['patient_online'] is False
    assert client.get(path, headers=user).json()['status'] == 'pending'
    client.post(path+'/heartbeat', headers=user)
    assert client.get(path, headers=user).json()['patient_online'] is True


def test_web_audio_chat_summary_and_purchase(integrated, monkeypatch):
    client, factory, _ = integrated
    user, pharma = identity('user', 'u'), identity('pharmacist', 'p')
    for url in ['/app/splash/', '/app/chatbot/', '/app/video-consult-entry/', '/app/map-screen/style.css', '/chat-test', '/pharmacist', '/kiosk/start/', '/scan', '/moyak-unified.js']:
        assert client.get(url).status_code == 200
    assert client.get('/client-config').json()['local_identity'] is True
    r = client.post('/consultations', headers=user, json={'user_id': 'u', 'chat_summary': 'prior chatbot record'})
    assert r.status_code == 200, r.text
    cid = r.json()['id']; path='/consultations/' + cid
    assert not client.get(path+'/presence', headers=user).json()['pharmacist_in_room']
    assert client.post(path+'/session/claim', headers=pharma).status_code == 200
    assert client.get(path+'/presence', headers=user).json()['pharmacist_in_room']
    body={'client_id':'legacy-1','sender_id':'u','sender_role':'user','content':'test chat'}
    sent=client.post(path+'/messages', headers=user, json=body)
    assert sent.status_code == 201, sent.text
    assert sent.json()['text'] == sent.json()['content'] == 'test chat'
    assert client.post(path+'/messages', headers=user,json=body).json()['id'] == sent.json()['id']
    assert client.post(path+'/messages', headers=pharma,json=body).status_code == 403
    with client.websocket_connect(path+'/messages/ws') as ws:
        ws.send_json({'sender_id':'u','sender_role':'user','after':0})
        assert ws.receive_json()['items'][0]['content'] == 'test chat'
    for headers in [user,pharma]:
        assert client.post(path+'/session/consent',headers=headers).status_code == 200
    r=client.post(path+'/audio?client_id=clip-1&start_ms=0',headers={**user,'Content-Type':'audio/wav'},content=b'test audio')
    assert r.status_code == 202, r.text
    monkeypatch.setattr(ai,'transcribe',lambda *args:'test transcript')
    assert worker.process_one(factory)
    decision=client.post(path+'/decision',headers=pharma,json={'pharmacist_id':'p','approve':True,
        'drug_item_seq':'drug-1','drug_item_name':'test medicine','price':100})
    assert decision.status_code == 200, decision.text
    assert client.post(path+'/end',headers=user).status_code == 403
    ended=client.post(path+'/end',headers=pharma)
    assert ended.status_code == 200, ended.text
    assert ended.json()['summary'] is None and ended.json()['summary_status']=='queued'
    content={key:[] for key in ['symptoms','discussion','medication_guidance','precautions','follow_up','needs_verification']}
    content['discussion']=['review draft']
    def summarize(source):
        assert source['prior_chat']=='prior chatbot record'
        assert source['transcript'][0]['text']=='test transcript'
        assert source['chat'][0]['text']=='test chat'
        return content
    monkeypatch.setattr(ai,'summarize',summarize)
    assert worker.process_one(factory)
    assert client.get(path,headers=user).json()['summary'] is None
    assert client.get(path+'/summary',headers=user).json()['content'] is None
    assert client.post(path+'/summary/publish',headers=user,json=content).status_code == 403
    assert client.post(path+'/summary/publish',headers=pharma,json=content).status_code == 200
    assert 'review draft' in client.get(path,headers=user).json()['summary']
    qr=client.post('/vending/machines/M001/rotate-qr').json()
    assert client.post('/vending/scan',headers=user,json={'machine_id':'M001','qr_token':qr['qr_token'],'user_id':'u'}).status_code == 200
    purchase=decision.json()['approved_purchase_id']
    assert client.get('/vending/purchases?user_id=u',headers=user).status_code == 200
    assert client.post('/vending/purchases/pay',json={'machine_id':'M001','purchase_ids':[purchase]}).json()['total_amount']==100
    assert client.post('/vending/dispense',json={'machine_id':'M001','purchase_id':purchase}).json()['status']=='dispensed'


def test_team_style_identity_outside_local_mode(integrated, monkeypatch):
    """배포(로컬 시연 모드 아님)에서도 요청이 보낸 ID·역할로 동작한다 — Flutter 앱용 팀 방식."""
    client, _, _ = integrated
    monkeypatch.delenv('MOYAK_LOCAL_IDENTITY')
    monkeypatch.setenv('CORS_ORIGINS', 'https://flutter.example')
    remote = TestClient(main.app, base_url='http://api.example', client=('203.0.113.5', 1234))
    assert not remote.get('/client-config').json()['local_identity']
    user, pharma = identity('user', 'u'), identity('pharmacist', 'p')
    # 팀 규격: 헤더 없이 바디만으로 상담 생성
    cid = remote.post('/consultations', json={'user_id': 'u'}).json()['id']
    path = '/consultations/' + cid
    assert remote.post('/consultations', headers=user, json={'user_id': 'other'}).status_code == 403
    assert remote.get(path, headers=identity('user', 'other')).status_code == 403
    # 약사 배정 전 사용자 채팅은 거절, 약사가 첫 메시지를 보내면 자동 배정
    assert remote.post(path+'/messages', json={'sender_id': 'u', 'sender_role': 'user', 'content': 'hi'}).status_code == 409
    sent = remote.post(path+'/messages', json={'sender_id': 'p', 'sender_role': 'pharmacist', 'content': 'hello'})
    assert sent.status_code == 201, sent.text
    assert remote.post(path+'/messages', json={'sender_id': 'p2', 'sender_role': 'pharmacist', 'content': 'x'}).status_code == 403
    assert remote.post(path+'/messages', json={'content': 'no sender'}).status_code == 401
    assert remote.post(path+'/messages', headers=user, json={'sender_id': 'p', 'content': 'spoof'}).status_code == 403
    # 신원 없이 목록 폴링(팀 규격), 신원이 있으면 참여자 검사
    assert remote.get(path+'/messages').json()[0]['content'] == 'hello'
    assert remote.get(path+'/messages', headers=identity('user', 'other')).status_code == 403
    assert remote.get('/consultations/unknown/messages').status_code == 404
    # 새 기능(동의·요약 등)은 신원 헤더 필수
    assert remote.post(path+'/session/consent').status_code == 401
    assert remote.post(path+'/session/consent', headers=user).status_code == 200
    # 약사 결정은 바디의 pharmacist_id로도 가능, 다른 약사는 불가
    body = {'pharmacist_id': 'p', 'approve': False, 'reason': 'test'}
    assert remote.post(path+'/decision', json={**body, 'pharmacist_id': 'p2'}).status_code == 409
    assert remote.post(path+'/decision', json=body).json()['status'] == 'rejected'
    assert remote.post(path+'/end').status_code == 401
    # WebSocket: 모바일(Origin 없음)·등록된 웹 출처만 허용
    monkeypatch.setenv('CORS_ORIGIN_REGEX', r'http://localhost(:\d+)?')
    for headers in [{}, {'Origin': 'https://flutter.example'}, {'Origin': 'http://localhost:51234'}]:
        with remote.websocket_connect(path+'/messages/ws', headers=headers) as ws:
            ws.send_json({'sender_id': 'u', 'sender_role': 'user'})
            assert ws.receive_json()['items'][0]['content'] == 'hello'
    with pytest.raises(WebSocketDisconnect):
        with remote.websocket_connect(path+'/messages/ws', headers={'Origin': 'https://evil.example'}):
            pass
    with remote.websocket_connect('/consultations/unknown/messages/ws') as ws:
        ws.send_json({'sender_id': 'u', 'sender_role': 'user'})
        assert ws.receive()['code'] == 1008
    remote.close()


def test_local_origin_boundary(integrated):
    client, _, _ = integrated
    assert client.get('/client-config',headers={'Origin':'https://other.example'}).status_code==403


def test_map_merges_inventory_and_product_stock_without_duplicates(integrated):
    client, factory, _ = integrated
    with factory() as db:
        db.add(VendingMachine(id='M001',name='machine',latitude=37.5,longitude=126.8))
        db.add(Product(machine_id='M001',item_seq='same',item_name='product',company='c',category='c',price=10,stock=3))
        db.add(VendingInventory(machine_id='M001',drug_id='same',drug_name='old',stock_count=99))
        db.add(VendingInventory(machine_id='M001',drug_id='other',drug_name='other',stock_count=4))
        db.commit()
    rows=client.get('/api/v1/map/machines?lat=37.5&lng=126.8').json()['items']
    assert rows[0]['stock_count']==7 and rows[0]['item_count']==2
    assert rows[0]['code']=='M001' and rows[0]['distance_m']==0
    detail=client.get('/api/v1/map/machines/M001').json()
    assert {r['item_seq'] for r in detail['items']}=={'same','other'}
    assert client.get('/api/v1/map/machines/M001?lat=37').status_code==422
    assert client.get('/api/v1/map/machines/missing').status_code==404


def test_schema_validation_is_read_only(integrated):
    _, _, engine=integrated
    with engine.begin() as conn:
        assert schema_problems(conn)==[]
        conn.execute(text('ALTER TABLE approved_purchases DROP COLUMN paid_at'))
        assert 'missing column approved_purchases.paid_at' in schema_problems(conn)
        assert 'missing column approved_purchases.paid_at' in schema_problems(conn)
