from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from src.api.routes import map as routes


def test_map_only_returns_active_positioned_public_data():
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE vending_machines (id TEXT, name TEXT, address TEXT, latitude FLOAT, longitude FLOAT, is_active BOOL, qr_token TEXT)'))
        conn.execute(text('CREATE TABLE vending_inventory (id INTEGER, machine_id TEXT, stock_count INTEGER)'))
        conn.execute(text("INSERT INTO vending_machines VALUES ('a','A','address',37,127,1,'private'),('b','B','',37,127,0,'private'),('c','C','',NULL,NULL,1,'private')"))
        conn.execute(text("INSERT INTO vending_inventory VALUES (1,'a',3),(2,'a',4)"))
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.map_engine] = lambda: engine
    with TestClient(app) as client:
        response = client.get('/api/v1/map/machines')
    assert response.status_code == 200
    items = response.json()['items']
    assert len(items) == 1
    assert items[0]['stock_count'] == 7
    assert items[0]['item_count'] == 2
    assert 'qr_token' not in items[0]
    engine.dispose()


def test_map_database_errors_do_not_expose_credentials():
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.map_engine] = lambda: engine
    with TestClient(app) as client:
        response = client.get('/api/v1/map/machines')
    assert response.status_code == 503
    assert 'SELECT' not in response.text
    engine.dispose()
