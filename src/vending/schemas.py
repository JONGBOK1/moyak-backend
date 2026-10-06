from pydantic import BaseModel, ConfigDict, Field


class NearbyMachineResponse(BaseModel):
    machine_id: str
    machine_name: str
    address: str
    latitude: float
    longitude: float
    distance_m: float
    distance_km: float
    is_active: bool
    inventory_status: str


class InventoryItemResponse(BaseModel):
    drug_id: str
    drug_name: str
    category: str
    price: int = Field(ge=0)
    stock_count: int = Field(ge=0)
    is_sold_out: bool


class InventoryResponse(BaseModel):
    machine_id: str
    machine_name: str
    items: list[InventoryItemResponse]


class MachineSeedRequest(BaseModel):
    machine_id: str = Field(min_length=1)
    machine_name: str = Field(min_length=1)
    address: str = ""
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    is_active: bool = True


class InventorySeedRequest(BaseModel):
    drug_id: str = Field(min_length=1)
    drug_name: str = Field(min_length=1)
    category: str = "기타"
    price: int = Field(ge=0)
    stock_count: int = Field(ge=0)