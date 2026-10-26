"""Profile roundtrip and role/owner boundaries. Isolated SQLite, no Supabase writes."""
from datetime import datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src import config
from src.accounts import users
from src.api.routes import care_profile, consultation
from src.consult import video
from src.consult.auth import issue_token
from src.consult.db import Base, get_db
from src.consult.models import ConsultationRequest


@pytest.fixture
def setup(monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    users.create_local_table(engine)
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE users ADD COLUMN care_note VARCHAR(500)'))
        conn.execute(text('''CREATE TABLE user_conditions (
            id VARCHAR PRIMARY KEY, user_id VARCHAR NOT NULL, condition_type VARCHAR NOT NULL,
            label TEXT NOT NULL, note TEXT, ingr_code VARCHAR, created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL, is_sample BOOLEAN NOT NULL DEFAULT 0)'''))
        conn.execute(text('''CREATE TABLE user_medications (
            id VARCHAR PRIMARY KEY, user_id VARCHAR NOT NULL, drug_name TEXT NOT NULL,
            item_seq VARCHAR, source VARCHAR NOT NULL, dosage_note TEXT,
            started_on DATE, ended_on DATE, created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL, is_sample BOOLEAN NOT NULL DEFAULT 0)'''))
    factory = sessionmaker(bind=engine)
    uid = str(uuid4())
    with factory() as db:
        users.add(db, uid, 'patient@example.test', 'patient')
        db.add(ConsultationRequest(id='c1', user_id=uid, chat_summary='test', room_url='https://test.daily.co/room'))
        db.commit()
    monkeypatch.setattr(config, 'CONSULT_AUTH_SECRET', 'test-secret-' * 4)
    monkeypatch.setattr(video, 'pharmacist_token', lambda *a: 'token')
    app = FastAPI()
    app.include_router(care_profile.router)
    app.include_router(consultation.router)
    def dependency():
        with factory() as db: yield db
    app.dependency_overrides[get_db] = dependency
    headers = {'Authorization': 'Bearer ' + issue_token(uid, 'user')}
    with TestClient(app) as client:
        yield client, factory, uid, headers
    engine.dispose()


def test_roundtrip_existing_structured_tables_and_stale_revision(setup):
    client, factory, uid, headers = setup
    original = client.get('/auth/me/care-profile', headers=headers).json()
    payload = dict(revision=original['revision'], care_note='상담 시 확인 부탁드립니다',
                   conditions=[dict(condition_type='allergy', label='페니실린', note='발진')],
                   medications=[dict(drug_name='복용약 A', dosage_note='하루 1회')])
    result = client.patch('/auth/me/care-profile', headers=headers, json=payload)
    assert result.status_code == 200, result.text
    saved = result.json()
    assert saved['conditions'][0]['label'] == '페니실린'
    assert saved['medications'][0]['source'] == 'manual'
    assert client.patch('/auth/me/care-profile', headers=headers, json=payload).status_code == 409
    assert client.get('/auth/me/care-profile', headers=headers).json() == saved


def test_manual_removal_preserves_purchase_history_and_rejects_foreign_id(setup):
    client, factory, uid, headers = setup
    now = datetime.now()
    with factory() as db:
        for source in ['manual', 'purchase', 'prescription']:
            db.execute(text('INSERT INTO user_medications (id,user_id,drug_name,source,created_at,updated_at) VALUES (:id,:uid,:name,:src,:now,:now)'),
                       dict(id=str(uuid4()), uid=uid, name=source, src=source, now=now))
        db.commit()
    data = client.get('/auth/me/care-profile', headers=headers).json()
    bad = dict(revision=data['revision'], conditions=[dict(id=str(uuid4()), condition_type='disease', label='다른 사람 항목')])
    assert client.patch('/auth/me/care-profile', headers=headers, json=bad).status_code == 422
    saved = client.patch('/auth/me/care-profile', headers=headers, json={'revision': data['revision'], 'medications': [], 'conditions': []})
    assert saved.status_code == 200, saved.text
    assert {r['source'] for r in saved.json()['medications']} == {'purchase', 'prescription'}
    with factory() as db:
        assert db.execute(text("SELECT ended_on FROM user_medications WHERE source='manual'")).scalar() is not None


def test_auth_owner_and_assigned_pharmacist_boundaries(setup):
    client, _, uid, headers = setup
    url = '/auth/me/care-profile'
    assert client.get(url).status_code == 401
    assert client.get(url, headers={'X-Moyak-User-Id': uid, 'X-Moyak-Role': 'user'}).status_code == 401
    assert client.get('/consultations/c1/care-profile', headers=headers).status_code == 403
    pharm = {'X-Moyak-User-Id': 'p1', 'X-Moyak-Role': 'pharmacist'}
    assert client.get('/consultations/c1/care-profile', headers=pharm).status_code == 200
    assert client.post('/consultations/c1/pharmacist-token', headers=headers).status_code == 403
    assert client.post('/consultations/c1/pharmacist-token', headers=pharm).status_code == 200
    other = {**pharm, 'X-Moyak-User-Id': 'p2'}
    assert client.get('/consultations/c1/care-profile', headers=other).status_code == 403
    assert client.post('/consultations/c1/pharmacist-token', headers=other).status_code == 409


def test_label_change_invalidates_only_related_code(setup):
    client, factory, uid, headers = setup
    cid = str(uuid4())
    with factory() as db:
        db.execute(text('INSERT INTO user_conditions (id,user_id,condition_type,label,ingr_code,created_at,updated_at) VALUES (:id,:uid,\'allergy\',\'A\',\'ingredient-a\',:now,:now)'), dict(id=cid, uid=uid, now=datetime.now()))
        db.commit()
    data = client.get('/auth/me/care-profile', headers=headers).json()
    data['conditions'][0]['note'] = '메모만 수정'
    saved = client.patch('/auth/me/care-profile', headers=headers, json=data).json()
    assert saved['conditions'][0]['ingr_code'] == 'ingredient-a'
    saved['conditions'][0]['label'] = 'B'
    result = client.patch('/auth/me/care-profile', headers=headers, json=saved).json()
    assert result['conditions'][0]['ingr_code'] is None
