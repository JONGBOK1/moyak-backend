import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from src.api.routes import drugs
from src.consult.db import get_db


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE drugs (item_seq TEXT PRIMARY KEY, item_name TEXT, efcy TEXT, private_extra TEXT)"))
        conn.execute(text("INSERT INTO drugs VALUES ('1', '가상약A', 'sample', 'do_not_expose'), ('2', '가상약B', NULL, NULL)"))
        conn.execute(text("CREATE TABLE drug_ingredients (item_seq TEXT, ingr_code TEXT, ingr_name TEXT)"))
        conn.execute(text("INSERT INTO drug_ingredients VALUES ('1','A','성분A'),('2','B','성분B')"))
    factory = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(drugs.router)
    def dependency():
        with factory() as session:
            yield session
    app.dependency_overrides[get_db] = dependency
    with TestClient(app) as client:
        yield client
    engine.dispose()


def test_catalog_pagination_and_public_fields(client):
    first = client.get('/api/v1/drugs?limit=1').json()
    assert first['has_more'] is True
    assert first['items'][0]['item_seq'] == '1'
    assert 'private_extra' not in first['items'][0]
    second = client.get('/api/v1/drugs?limit=1&offset=1').json()
    assert second['has_more'] is False and second['items'][0]['item_seq'] == '2'


def test_catalog_search_and_missing_drug(client):
    assert len(client.get('/api/v1/drugs?q=가상약A').json()['items']) == 1
    assert client.get('/api/v1/drugs?q=%25').json()['items'] == []
    assert client.get('/api/v1/drugs/unknown').status_code == 404


def test_related_data_is_filtered_and_missing_tables_are_explicit(client):
    assert client.get('/api/v1/drugs/1/ingredients').json()['items'][0]['ingr_code'] == 'A'
    assert client.get('/api/v1/drugs/1/pills').status_code == 503
    assert client.get('/api/v1/drugs/1/users').status_code == 422
