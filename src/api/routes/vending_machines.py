from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from src.consult.db import get_db
from src.vending import service
from src.vending.schemas import InventoryResponse, NearbyMachineResponse

router = APIRouter(prefix="/api/v1/vending-machines", tags=["vending-machines"])


@router.get("/nearby", response_model=list[NearbyMachineResponse])
def nearby_machines(
    lat: float = Query(..., ge=-90, le=90, description="현재 위도"),
    lng: float = Query(..., ge=-180, le=180, description="현재 경도"),
    radius: float = Query(3.0, gt=0, le=100, description="검색 반경(km)"),
    keyword: str | None = Query(None, min_length=1, description="자판기명 또는 주소 검색어"),
    db: Session = Depends(get_db),
) -> list[NearbyMachineResponse]:
    return service.list_nearby_machines(db, lat, lng, radius, keyword)


@router.get("/{machine_id}/inventory", response_model=InventoryResponse)
def machine_inventory(machine_id: str, db: Session = Depends(get_db)) -> InventoryResponse:
    try:
        return service.get_inventory(db, machine_id)
    except service.MachineNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error