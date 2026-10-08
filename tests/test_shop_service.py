import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.consult import shop
from src.consult.db import Base
from src.consult.models import OrderStatus


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_list_products_seeds_demo_catalog_when_empty(db):
    products = shop.list_products(db, machine_id="M1")
    assert len(products) == len(shop.DEMO_CATALOG)


def test_list_products_does_not_duplicate_seed_on_second_call(db):
    shop.list_products(db, machine_id="M1")
    products = shop.list_products(db, machine_id="M1")
    assert len(products) == len(shop.DEMO_CATALOG)


def test_list_products_filters_by_category(db):
    products = shop.list_products(db, machine_id="M1", category="상처/연고")
    assert len(products) > 0
    assert all(p.category == "상처/연고" for p in products)


def test_create_order_deducts_stock_and_computes_total(db):
    products = shop.list_products(db, machine_id="M1")
    band = next(p for p in products if p.item_name == "대일밴드 뉴 표준형 20매")
    original_stock = band.stock

    order = shop.create_order(db, machine_id="M1", items=[{"product_id": band.id, "quantity": 2}])

    assert order.total_amount == band.price * 2
    assert order.status == OrderStatus.CART
    refreshed = db.get(type(band), band.id)
    assert refreshed.stock == original_stock - 2


def test_create_order_insufficient_stock_raises(db):
    products = shop.list_products(db, machine_id="M1")
    band = products[0]
    with pytest.raises(shop.InvalidStateError):
        shop.create_order(db, machine_id="M1", items=[{"product_id": band.id, "quantity": band.stock + 1}])


def test_create_order_unknown_product_raises(db):
    shop.list_products(db, machine_id="M1")
    with pytest.raises(shop.NotFoundError):
        shop.create_order(db, machine_id="M1", items=[{"product_id": "ghost", "quantity": 1}])


def test_create_order_empty_items_raises(db):
    with pytest.raises(shop.InvalidStateError):
        shop.create_order(db, machine_id="M1", items=[])


def test_pay_then_dispense_order(db):
    products = shop.list_products(db, machine_id="M1")
    order = shop.create_order(db, machine_id="M1", items=[{"product_id": products[0].id, "quantity": 1}])

    paid = shop.pay_order(db, order_id=order.id)
    assert paid.status == OrderStatus.PAID
    assert paid.paid_at is not None

    dispensed = shop.dispense_order(db, order_id=order.id)
    assert dispensed.status == OrderStatus.DISPENSED
    assert dispensed.dispensed_at is not None


def test_dispense_before_pay_raises(db):
    products = shop.list_products(db, machine_id="M1")
    order = shop.create_order(db, machine_id="M1", items=[{"product_id": products[0].id, "quantity": 1}])
    with pytest.raises(shop.InvalidStateError):
        shop.dispense_order(db, order_id=order.id)


def test_pay_order_twice_raises(db):
    products = shop.list_products(db, machine_id="M1")
    order = shop.create_order(db, machine_id="M1", items=[{"product_id": products[0].id, "quantity": 1}])
    shop.pay_order(db, order_id=order.id)
    with pytest.raises(shop.InvalidStateError):
        shop.pay_order(db, order_id=order.id)


def test_get_order_not_found_raises(db):
    with pytest.raises(shop.NotFoundError):
        shop.get_order(db, order_id="ghost")
