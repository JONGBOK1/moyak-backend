from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.routes import map as routes
from src.consult import map_seed
from src.consult.db import Base
from src.consult.models import Product, VendingMachine


def _engine():
    return create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})


def _client(engine):
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.map_engine] = lambda: engine
    return TestClient(app)


def _seed_basic(engine):
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    db.add_all([
        VendingMachine(id="a", name="A", address="address", latitude=37.0, longitude=127.0, is_active=True, qr_token="private"),
        VendingMachine(id="b", name="B", latitude=37.0, longitude=127.0, is_active=False),  # 비활성 → 제외
        VendingMachine(id="c", name="C", is_active=True),  # 좌표 없음 → 제외
        VendingMachine(id="far", name="Far", latitude=37.1, longitude=127.0, is_active=True),
    ])
    db.add_all([
        Product(machine_id="a", item_seq="1", item_name="밴드", company="x", category="c", price=1000, stock=3),
        Product(machine_id="a", item_seq="2", item_name="거즈", company="x", category="c", price=2000, stock=4),
    ])
    db.commit()
    db.close()


def test_map_only_returns_active_positioned_public_data(monkeypatch):
    monkeypatch.delenv("MAP_DATABASE_URL", raising=False)
    engine = _engine()
    _seed_basic(engine)
    with _client(engine) as client:
        response = client.get("/api/v1/map/machines")
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "local"
    items = {m["id"]: m for m in body["items"]}
    assert set(items) == {"a", "far"}
    assert items["a"]["stock_count"] == 7
    assert items["a"]["item_count"] == 2
    assert "qr_token" not in items["a"]
    assert items["a"]["distance_m"] is None
    engine.dispose()


def test_map_sorts_by_distance_when_location_given(monkeypatch):
    monkeypatch.delenv("MAP_DATABASE_URL", raising=False)
    engine = _engine()
    _seed_basic(engine)
    with _client(engine) as client:
        items = client.get("/api/v1/map/machines", params={"lat": 37.1, "lng": 127.0}).json()["items"]
        assert [m["id"] for m in items] == ["far", "a"]
        assert items[0]["distance_m"] == 0
        assert 11000 < items[1]["distance_m"] < 11300  # 위도 0.1도 ≈ 11.1km
        assert client.get("/api/v1/map/machines", params={"lat": 37.1}).status_code == 422
    engine.dispose()


def test_machine_detail_lists_inventory_and_404s(monkeypatch):
    monkeypatch.delenv("MAP_DATABASE_URL", raising=False)
    engine = _engine()
    _seed_basic(engine)
    with _client(engine) as client:
        detail = client.get("/api/v1/map/machines/a").json()
        assert {i["item_name"]: i["stock"] for i in detail["items"]} == {"밴드": 3, "거즈": 4}
        assert "qr_token" not in detail
        assert client.get("/api/v1/map/machines/b").status_code == 404  # 비활성
        assert client.get("/api/v1/map/machines/nope").status_code == 404
    engine.dispose()


def test_map_database_errors_do_not_expose_credentials(monkeypatch):
    monkeypatch.delenv("MAP_DATABASE_URL", raising=False)
    engine = _engine()  # 테이블 없음 → DB 오류
    with _client(engine) as client:
        response = client.get("/api/v1/map/machines")
    assert response.status_code == 503
    assert "SELECT" not in response.text
    engine.dispose()


def test_seed_demo_machines_places_machines_around_center_once(monkeypatch):
    monkeypatch.setattr(map_seed.config, "DEMO_MAP_CENTER", "35.0,129.0")
    engine = _engine()
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    map_seed.seed_demo_machines(db)
    m1 = db.get(VendingMachine, "M001")
    assert abs(m1.latitude - 35.0) < 0.02 and abs(m1.longitude - 129.0) < 0.02
    assert db.query(Product).filter(Product.machine_id == "M001").count() > 0
    assert all(p.stock == 0 for p in db.query(Product).filter(Product.machine_id == "M005"))  # 품절 시연용

    m1.latitude = 1.0  # 운영자가 고친 위치는 다시 시드해도 덮어쓰지 않는다
    db.commit()
    map_seed.seed_demo_machines(db)
    assert db.get(VendingMachine, "M001").latitude == 1.0
    db.close()
    engine.dispose()
