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



def machine_medicines(db, machine_code):
    """키오스크(code)의 의약품 재고 [{item_seq, item_name, stock, price}] — 약사가 처방할 수 있는 약 목록.

    machine_inventory가 없는 DB(로컬 SQLite 시연)에서는 None을 돌려준다 → 제한 없이 기존처럼 검색해서 처방.
    약 이름은 허가정보(drug_permissions) → e약은요(drugs) 순으로 찾고, 없으면 품목코드를 그대로 쓴다.
    """
    from sqlalchemy import inspect, text

    insp = inspect(db.connection())
    names = set(insp.get_table_names())
    if not {"machine_inventory", "vending_machines"} <= names:
        return None
    if "id" not in {c["name"] for c in insp.get_columns("vending_machines")}:  # 로컬 SQLite 자판기 테이블(code만 있음)
        return None
    name_sources = [t for t in ("drug_permissions", "drugs") if t in names]
    joins = "".join(f" LEFT JOIN {t} n{i} ON n{i}.item_seq = i.item_seq" for i, t in enumerate(name_sources))
    name_expr = "COALESCE(" + ", ".join([f"n{i}.item_name" for i in range(len(name_sources))] + ["i.item_seq"]) + ")"
    rows = db.execute(text(
        f"SELECT i.item_seq, {name_expr} AS item_name, i.stock, i.price FROM machine_inventory i "
        f"JOIN vending_machines m ON m.id = i.machine_id{joins} WHERE m.code = :code ORDER BY item_name"
    ), {"code": machine_code}).mappings().all()
    seen, items = set(), []
    for r in rows:  # 허가정보에 같은 품목코드가 여러 줄일 수 있어 첫 줄만
        if r["item_seq"] in seen:
            continue
        seen.add(r["item_seq"])
        items.append({"item_seq": str(r["item_seq"]), "item_name": r["item_name"], "stock": int(r["stock"] or 0),
                      "price": int(r["price"]) if r["price"] is not None else None})
    return items
