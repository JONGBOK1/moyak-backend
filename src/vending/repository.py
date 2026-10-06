from math import cos, radians

from sqlalchemy import Float, case, func, literal
from sqlalchemy.orm import Session

from src.consult.models import VendingInventory, VendingMachine

EARTH_RADIUS_KM = 6371.0088


def _distance_km(latitude: float, longitude: float):
    lat1 = func.radians(literal(latitude))
    lon1 = func.radians(literal(longitude))
    lat2 = func.radians(VendingMachine.latitude)
    lon2 = func.radians(VendingMachine.longitude)
    a = func.pow(func.sin((lat2 - lat1) / 2), 2) + func.cos(lat1) * func.cos(lat2) * func.pow(
        func.sin((lon2 - lon1) / 2), 2
    )
    return literal(EARTH_RADIUS_KM) * 2 * func.asin(func.sqrt(a))


def find_nearby_machines(db: Session, latitude: float, longitude: float, radius_km: float, keyword: str | None):
    lat_delta = radius_km / 111.045
    lon_delta = radius_km / (111.045 * max(cos(radians(latitude)), 0.01))
    distance_km = _distance_km(latitude, longitude).cast(Float).label("distance_km")

    query = (
        db.query(VendingMachine, distance_km)
        .filter(VendingMachine.latitude.is_not(None), VendingMachine.longitude.is_not(None))
        .filter(VendingMachine.latitude.between(latitude - lat_delta, latitude + lat_delta))
        .filter(VendingMachine.longitude.between(longitude - lon_delta, longitude + lon_delta))
    )
    if keyword:
        query = query.filter(
            VendingMachine.name.ilike(f"%{keyword}%") | VendingMachine.address.ilike(f"%{keyword}%")
        )
    return query.filter(distance_km <= radius_km).order_by(distance_km.asc()).all()


def get_machine(db: Session, machine_id: str) -> VendingMachine | None:
    return db.get(VendingMachine, machine_id)


def list_inventory(db: Session, machine_id: str) -> list[VendingInventory]:
    return (
        db.query(VendingInventory)
        .filter(VendingInventory.machine_id == machine_id)
        .order_by(VendingInventory.drug_name.asc())
        .all()
    )


def upsert_machine(db: Session, values: dict) -> VendingMachine:
    machine = db.get(VendingMachine, values["id"])
    if machine is None:
        machine = VendingMachine(id=values["id"])
        db.add(machine)
    for key, value in values.items():
        setattr(machine, key, value)
    db.commit()
    db.refresh(machine)
    return machine


def upsert_inventory(db: Session, machine_id: str, values: dict) -> VendingInventory:
    item = (
        db.query(VendingInventory)
        .filter(VendingInventory.machine_id == machine_id, VendingInventory.drug_id == values["drug_id"])
        .one_or_none()
    )
    if item is None:
        item = VendingInventory(machine_id=machine_id, **values)
        db.add(item)
    else:
        for key, value in values.items():
            setattr(item, key, value)
    db.commit()
    db.refresh(item)
    return item