"""Regression tests use disposable SQLite only; never connect to Supabase."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.api.routes import consultation
from src.api.limiter import limiter
from src.consult import service, shop
from src.consult.db import Base, get_db
from src.consult.models import ApprovedPurchase, ConsultationRequest, ConsultationMessage, Product, KioskOrder, VendingMachine


@pytest.fixture
def factory(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + str(tmp_path / 'regression.db'), connect_args={'timeout': 15})
    Base.metadata.create_all(engine)
    monkeypatch.setattr(service.video, 'create_room', lambda: None)
    yield sessionmaker(bind=engine)
    engine.dispose()


def test_message_api_retry_returns_same_row_and_rejects_changed_content(factory):
    with factory() as db:
        cid = service.create_consultation(db, user_id='user').id
    app = FastAPI()
    app.state.limiter = limiter
    app.include_router(consultation.router)
    def dependency():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = dependency
    with TestClient(app) as client:
        payload = dict(sender_role='user', sender_id='user', content='hello', client_id='retry-1')
        first = client.post(f'/consultations/{cid}/messages', json=payload)
        again = client.post(f'/consultations/{cid}/messages', json=payload)
        assert first.status_code == again.status_code == 200
        assert first.json()['id'] == again.json()['id']
        assert first.json()['client_id'] == 'retry-1'
        conflict = client.post(f'/consultations/{cid}/messages', json={**payload, 'content': 'changed'})
        assert conflict.status_code == 409
    with factory() as db:
        assert db.query(ConsultationMessage).count() == 1


def test_cancel_expiry_and_partial_failure_restore_stock(factory):
    with factory() as db:
        product = shop.list_products(db, 'VM-001')[0]
        initial = product.stock
        order = shop.create_order(db, 'VM-001', [{'product_id': product.id, 'quantity': 2}])
        shop.cancel_order(db, order.id)
        shop.cancel_order(db, order.id)
        assert db.get(Product, product.id).stock == initial
        order = shop.create_order(db, 'VM-001', [{'product_id': product.id, 'quantity': 3}])
        order.created_at = shop._now() - timedelta(minutes=11)
        db.commit()
        shop.expire_orders(db)
        shop.expire_orders(db)
        assert db.get(Product, product.id).stock == initial
        with pytest.raises(shop.InvalidStateError):
            shop.pay_order(db, order.id)
        with pytest.raises(shop.InvalidStateError):
            shop.create_order(db, 'VM-001', [{'product_id': product.id, 'quantity': 1},
                {'product_id': product.id, 'quantity': initial + 1}])
        assert db.get(Product, product.id).stock == initial
        assert db.query(KioskOrder).count() == 2


def test_two_carts_cannot_reserve_last_item_twice(factory):
    with factory() as db:
        product = shop.list_products(db, 'VM-001')[0]
        product.stock = 1
        db.commit()
        pid = product.id
    barrier = Barrier(2)
    def reserve():
        with factory() as db:
            barrier.wait(timeout=5)
            try:
                shop.create_order(db, 'VM-001', [{'product_id': pid, 'quantity': 1}])
                return True
            except shop.InvalidStateError:
                return False
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: reserve(), range(2)))
    assert sorted(results) == [False, True]
    with factory() as db:
        assert db.get(Product, pid).stock == 0
        assert db.query(KioskOrder).count() == 1


USER = '00000000-0000-4000-8000-000000000001'
MACHINE = '00000000-0000-4000-8000-000000000002'


@pytest.fixture
def inventory_db(factory, monkeypatch):
    with factory() as db:
        # Supabase code/UUID distinction reproduced without touching a real database.
        db.execute(text('ALTER TABLE vending_machines ADD COLUMN id VARCHAR'))
        db.execute(text('CREATE TABLE users (id VARCHAR PRIMARY KEY)'))
        db.execute(text('CREATE TABLE machine_inventory (machine_id VARCHAR, item_seq VARCHAR, stock INTEGER CHECK(stock >= 0), updated_at DATETIME, PRIMARY KEY(machine_id,item_seq))'))
        db.execute(text('CREATE TABLE purchases (id VARCHAR PRIMARY KEY, user_id VARCHAR, machine_id VARCHAR, item_seq VARCHAR, item_name TEXT, quantity INTEGER, price NUMERIC, purchased_at DATETIME)'))
        db.add(VendingMachine(id='VM-001', name='machine'))
        db.commit()
        db.execute(text('UPDATE vending_machines SET id=:id'), {'id': MACHINE})
        db.execute(text('INSERT INTO users VALUES (:id)'), {'id': USER})
        db.execute(text("INSERT INTO machine_inventory VALUES (:id, 'drug', 2, NULL)"), {'id': MACHINE})
        c = service.create_consultation(db, USER)
        c = service.decide_consultation(db, c.id, 'pharma', True, drug_item_seq='drug', drug_item_name='medicine', price=100)
        purchase = c.purchase
        purchase.paid_at = service._now()
        db.commit()
        pid = purchase.id
    monkeypatch.setattr(service, 'is_shared_db', lambda db: True)
    return factory, pid


def test_supabase_dispense_stock_history_and_repeat(inventory_db):
    factory, pid = inventory_db
    with factory() as db:
        assert service.dispense(db, pid, 'VM-001').status == 'dispensed'
        with pytest.raises(service.InvalidStateError):
            service.dispense(db, pid, 'VM-001')
        assert db.execute(text('SELECT stock FROM machine_inventory')).scalar_one() == 1
        row = db.execute(text('SELECT user_id,machine_id,quantity,price FROM purchases')).one()
        assert tuple(row) == (USER, MACHINE, 1, 100)


def test_failed_dispense_rolls_back_status_and_inventory(inventory_db):
    factory, pid = inventory_db
    with factory() as db:
        db.execute(text('CREATE TRIGGER fail_history BEFORE INSERT ON purchases BEGIN SELECT RAISE(ABORT, "test failure"); END'))
        db.commit()
        with pytest.raises(Exception, match='test failure'):
            service.dispense(db, pid, 'VM-001')
        assert db.get(ApprovedPurchase, pid).status == 'pending'
        assert db.execute(text('SELECT stock FROM machine_inventory')).scalar_one() == 2
        assert db.execute(text('SELECT count(*) FROM purchases')).scalar_one() == 0


def test_no_stock_or_unknown_machine_cannot_complete(inventory_db):
    factory, pid = inventory_db
    with factory() as db:
        with pytest.raises(service.InvalidStateError):
            service.dispense(db, pid, 'M001')
        db.execute(text('UPDATE machine_inventory SET stock=0'))
        db.commit()
        with pytest.raises(service.InvalidStateError):
            service.dispense(db, pid, 'VM-001')
        assert db.get(ApprovedPurchase, pid).status == 'pending'
        assert db.execute(text('SELECT count(*) FROM purchases')).scalar_one() == 0


def test_simultaneous_dispense_records_only_once(inventory_db):
    factory, pid = inventory_db
    barrier = Barrier(2)
    def dispense():
        with factory() as db:
            barrier.wait(timeout=5)
            try:
                service.dispense(db, pid, 'VM-001')
                return True
            except service.InvalidStateError:
                return False
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: dispense(), range(2)))
    assert sorted(results) == [False, True]
    with factory() as db:
        assert db.execute(text('SELECT stock FROM machine_inventory')).scalar_one() == 1
        assert db.execute(text('SELECT count(*) FROM purchases')).scalar_one() == 1


def test_expiry_never_releases_paid_stock(factory):
    with factory() as db:
        product = shop.list_products(db, 'VM-001')[0]
        initial = product.stock
        order = shop.create_order(db, 'VM-001', [{'product_id': product.id, 'quantity': 2}])
        shop.pay_order(db, order.id)
        order.created_at = shop._now() - timedelta(minutes=11)
        db.commit()
        shop.expire_orders(db)
        assert db.get(Product, product.id).stock == initial - 2
        with pytest.raises(shop.InvalidStateError):
            shop.cancel_order(db, order.id)
        assert db.get(KioskOrder, order.id).status == 'paid'
