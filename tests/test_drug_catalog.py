import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from src.api.routes import drugs
from src.api.routes import drug_search
from src.consult.db import get_db
from src.drugs.database import get_catalog_db
from src.drugs import repository


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(repository, 'LEGACY_PERMISSION_IDS', tuple(f'p{i}' for i in range(1, 10)))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE drugs (item_seq TEXT PRIMARY KEY, item_name TEXT, efcy TEXT, private_extra TEXT)"))
        conn.execute(text("INSERT INTO drugs VALUES ('1', '가상약A', 'sample', 'do_not_expose'), ('2', '가상약B', NULL, NULL)"))
        conn.execute(text("""
            CREATE TABLE drug_permissions (
                item_seq TEXT NOT NULL, item_name TEXT, entp_name TEXT, bizrno TEXT,
                etc_otc_name TEXT
            )
        """))
        conn.execute(text("""
            INSERT INTO drug_permissions (item_seq, item_name, entp_name, bizrno, etc_otc_name)
            VALUES ('1', '중복된 약품', '기존 업체', NULL, '일반'),
                   ('p1', '권한전용품목1', '허가 업체1', '001', '일반'),
                   ('p2', '권한전용품목2', '허가 업체2', '002', '일반'),
                   ('p3', '권한전용품목3', '허가 업체3', '003', '일반'),
                   ('p4', '권한전용품목4', '허가 업체4', '004', '일반'),
                   ('p5', '권한전용품목5', '허가 업체5', '005', '일반'),
                   ('p6', '권한전용품목6', '허가 업체6', '006', '일반'),
                   ('p7', '권한전용품목7', '허가 업체7', '007', '일반'),
                   ('p8', '권한전용품목8', '허가 업체8', '008', '일반'),
                   ('p9', '권한전용품목9', '허가 업체9', '009', '일반'),
                   ('outside', '권한전용품목범위밖', '다른 업체', NULL, '일반')
        """))
        conn.execute(text("CREATE TABLE drug_ingredients (item_seq TEXT, ingr_code TEXT, ingr_name TEXT)"))
        conn.execute(text("INSERT INTO drug_ingredients VALUES ('1','A','성분A'),('2','B','성분B')"))
    factory = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(drugs.router)
    app.include_router(drug_search.router)
    def dependency():
        with factory() as session:
            yield session
    app.dependency_overrides[get_db] = dependency
    app.dependency_overrides[get_catalog_db] = dependency
    with TestClient(app) as client:
        yield client
    engine.dispose()


def test_catalog_pagination_and_public_fields(client):
    first = client.get('/api/v1/drugs?q=가상약&limit=1').json()
    assert first['has_more'] is True
    assert first['items'][0]['item_seq'] == '1'
    assert 'private_extra' not in first['items'][0]
    second = client.get('/api/v1/drugs?q=가상약&limit=1&offset=1').json()
    assert second['has_more'] is False and second['items'][0]['item_seq'] == '2'


def test_catalog_search_and_missing_drug(client):
    assert len(client.get('/api/v1/drugs?q=가상약A').json()['items']) == 1
    assert client.get('/api/v1/drugs?q=%25').json()['items'] == []
    assert client.get('/api/v1/drugs/unknown').status_code == 404


def test_catalog_search_includes_permissions_only_items_without_duplicate_drugs(client):
    response = client.get('/api/v1/drugs?q=권한전용품목')
    assert response.status_code == 200
    items = response.json()['items']
    assert len(items) == 9
    assert {item['item_seq'] for item in items} == {f'p{i}' for i in range(1, 10)}

    all_items = client.get('/api/v1/drugs?limit=100').json()['items']
    assert len([item for item in all_items if item['item_seq'] == '1']) == 1


def test_pharmacist_autocomplete_uses_database_and_includes_permissions_only_items(client):
    response = client.get('/drugs/search?q=권한전용품목')
    assert response.status_code == 200
    assert len(response.json()) == 9
    assert response.json()[0] == {
        'item_seq': 'p1',
        'item_name': '권한전용품목1',
        'company': '허가 업체1',
    }


def test_permission_only_catalog_item_can_be_fetched_by_item_seq(client):
    response = client.get('/api/v1/drugs/p1')
    assert response.status_code == 200
    assert response.json()['item_name'] == '권한전용품목1'
    assert response.json()['entp_name'] == '허가 업체1'


def test_permissions_outside_legacy_scope_are_not_exposed(client):
    assert client.get('/drugs/search?q=outside').json() == []
    assert client.get('/api/v1/drugs/outside').status_code == 404
    rows = client.get('/api/v1/drugs?limit=100').json()['items']
    assert len(rows) == 11
    assert all(row['item_seq'] != 'outside' for row in rows)


def test_drugs_name_wins_over_permission_duplicate(client):
    assert client.get('/drugs/search?q=중복된').json() == []
    assert client.get('/api/v1/drugs/1').json()['item_name'] == '가상약A'


def test_related_data_is_filtered_and_missing_tables_are_explicit(client):
    assert client.get('/api/v1/drugs/1/ingredients').json()['items'][0]['ingr_code'] == 'A'
    assert client.get('/api/v1/drugs/1/pills').status_code == 503
    assert client.get('/api/v1/drugs/1/users').status_code == 422
