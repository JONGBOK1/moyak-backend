from sqlalchemy.orm import Session

from src.vending import repository
from src.vending.schemas import InventoryItemResponse, InventoryResponse, NearbyMachineResponse


class MachineNotFoundError(Exception):
    pass


def _inventory_status(items) -> str:
    if not items:
        return "재고 없음"
    stocked = sum(item.stock_count > 0 for item in items)
    if stocked == 0:
        return "품절"
    if stocked < len(items):
        return "일부 품절"
    return "재고 있음"


def list_nearby_machines(
    db: Session, latitude: float, longitude: float, radius_km: float, keyword: str | None
) -> list[NearbyMachineResponse]:
    rows = repository.find_nearby_machines(db, latitude, longitude, radius_km, keyword)
    return [
        NearbyMachineResponse(
            machine_id=machine.id,
            machine_name=machine.name,
            address=machine.address,
            latitude=machine.latitude,
            longitude=machine.longitude,
            distance_m=round(distance_km * 1000, 1),
            distance_km=round(distance_km, 3),
            is_active=machine.is_active,
            inventory_status=_inventory_status(machine.inventory_items),
        )
        for machine, distance_km in rows
    ]


def get_inventory(db: Session, machine_id: str) -> InventoryResponse:
    machine = repository.get_machine(db, machine_id)
    if machine is None:
        raise MachineNotFoundError(f"자판기를 찾을 수 없습니다: {machine_id}")
    items = repository.list_inventory(db, machine_id)
    return InventoryResponse(
        machine_id=machine.id,
        machine_name=machine.name,
        items=[
            InventoryItemResponse(
                drug_id=item.drug_id,
                drug_name=item.drug_name,
                category=item.category,
                price=item.price,
                stock_count=item.stock_count,
                is_sold_out=item.stock_count == 0,
            )
            for item in items
        ],
    )