"""환자가 고른 키오스크 재고에 있는 약만 약사가 승인할 수 있는지 (2026-10-19).

팀 Supabase 구조(vending_machines.id UUID + code, machine_inventory.machine_id → vending_machines.id)를
임시 SQLite에 흉내 내서 확인한다. 실제 API 호출 없음.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.routes import consultation, vending
from src.consult import video
from src.consult.db import Base, get_db
from src.consult.models import VendingMachine

TYLENOL = ("202106092", "타이레놀정500밀리그람(아세트아미노펜)")


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:  # 팀 Supabase처럼: 자판기 내부 UUID id + 의약품 재고 + 허가정보 이름
        conn.execute(text("ALTER TABLE vending_machines ADD COLUMN id VARCHAR"))
        conn.execute(text("CREATE TABLE machine_inventory (machine_id VARCHAR, item_seq VARCHAR, stock INTEGER, price NUMERIC)"))
        conn.execute(text("CREATE TABLE drug_permissions (item_seq VARCHAR, item_name VARCHAR)"))
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add_all([VendingMachine(id="VM-001", name="동양미래대학교 학생회관"), VendingMachine(id="VM-002", name="고척점")])
        db.commit()
        db.execute(text("UPDATE vending_machines SET id = 'uuid-1' WHERE code = 'VM-001'"))
        db.execute(text("UPDATE vending_machines SET id = 'uuid-2' WHERE code = 'VM-002'"))
        db.execute(text("INSERT INTO machine_inventory VALUES ('uuid-1', :s, 12, 3500), ('uuid-1', '198700405', 0, 2000), "
                        "('uuid-2', '196800036', 5, 1500)"), {"s": TYLENOL[0]})
        db.execute(text("INSERT INTO drug_permissions VALUES (:s, :n), ('198700405', '베아제정')"), {"s": TYLENOL[0], "n": TYLENOL[1]})
        db.commit()
    monkeypatch.setattr(video, "create_room", lambda: None)
    app = FastAPI()
    app.include_router(consultation.router)
    app.include_router(vending.router)

    def dependency():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = dependency
    with TestClient(app) as c:
        yield c
    engine.dispose()


def approve(client, cid, seq, name, price=None):
    body = {"pharmacist_id": "p1", "approve": True, "drug_item_seq": seq, "drug_item_name": name}
    if price is not None:
        body["price"] = price
    return client.post(f"/consultations/{cid}/decision", json=body)


def test_medicines_list_shows_only_that_kiosk(client):
    data = client.get("/vending/machines/VM-001/medicines").json()
    assert data["restricted"] is True and data["machine_name"] == "동양미래대학교 학생회관"
    assert [(i["item_name"], i["stock"], i["price"]) for i in data["items"]] == [("베아제정", 0, 2000), (TYLENOL[1], 12, 3500)]
    assert client.get("/vending/machines/VM-999/medicines").status_code == 404


def test_consultation_keeps_selected_kiosk(client):
    c = client.post("/consultations", json={"user_id": "u1", "machine_code": "VM-001"}).json()
    assert c["machine_code"] == "VM-001"
    assert client.post("/consultations", json={"user_id": "u1", "machine_code": "VM-999"}).status_code == 404
    assert client.post("/consultations", json={"user_id": "u1"}).json()["machine_code"] is None


def test_only_in_stock_medicine_of_selected_kiosk_can_be_approved(client):
    cid = client.post("/consultations", json={"user_id": "u1", "machine_code": "VM-001"}).json()["id"]
    other_kiosk = approve(client, cid, "196800036", "판콜에이내복액")  # VM-002에만 있는 약
    assert other_kiosk.status_code == 409 and "VM-001" in other_kiosk.json()["detail"]
    assert approve(client, cid, "198700405", "베아제정").status_code == 409  # 재고 0개
    ok = approve(client, cid, *TYLENOL)  # 가격을 안 넣으면 키오스크 판매가
    assert ok.status_code == 200 and ok.json()["approved_drug_name"] == TYLENOL[1]
    purchase = client.get("/vending/purchases", params={"user_id": "u1"}).json()[0]
    assert purchase["price"] == 3500


def test_without_selected_kiosk_any_medicine_can_be_approved(client):
    cid = client.post("/consultations", json={"user_id": "u1"}).json()["id"]
    assert approve(client, cid, "196800036", "판콜에이내복액", price=1500).status_code == 200
