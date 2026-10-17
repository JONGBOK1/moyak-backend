"""Existing Supabase tables only: no create_all or schema mutations."""
import uuid
from sqlalchemy import MetaData, Table, select, update, insert


class InventoryError(ValueError):
    pass


def record_dispense(db, purchase, machine_code, now):
    metadata = MetaData()
    tables = {name: Table(name, metadata, autoload_with=db.connection())
              for name in ('vending_machines', 'users', 'machine_inventory', 'purchases')}
    machines, users, inventory, purchases = (tables[n] for n in tables)
    try:
        user_id = uuid.UUID(purchase.user_id)
    except ValueError:
        raise InventoryError('등록된 사용자 UUID로 상담을 신청해야 수령할 수 있습니다.') from None
    # Reflection uses native UUID objects on PostgreSQL, strings in SQLite tests.
    def identifier(column, value):
        return value if getattr(column.type, 'as_uuid', False) else str(value)
    user_id = identifier(users.c.id, user_id)
    if db.execute(select(users.c.id).where(users.c.id == user_id)).scalar_one_or_none() is None:
        raise InventoryError('등록된 사용자가 아닙니다.')
    machine_id = db.execute(select(machines.c.id).where(
        machines.c.code == machine_code, machines.c.is_active.is_(True))).scalar_one_or_none()
    if machine_id is None:
        raise InventoryError('운영 중인 자판기를 찾을 수 없습니다.')
    changed = db.execute(update(inventory).where(
        inventory.c.machine_id == machine_id,
        inventory.c.item_seq == purchase.drug_item_seq,
        inventory.c.stock >= 1).values(stock=inventory.c.stock - 1, updated_at=now)).rowcount
    if changed != 1:
        raise InventoryError('이 자판기에 해당 약품의 재고가 없습니다.')
    db.execute(insert(purchases).values(
        id=identifier(purchases.c.id, uuid.uuid4()), user_id=user_id, machine_id=machine_id,
        item_seq=purchase.drug_item_seq, item_name=purchase.drug_item_name,
        quantity=1, price=purchase.price or 0, purchased_at=now))
