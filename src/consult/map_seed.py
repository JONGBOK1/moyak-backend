"""지도 시연용 가상 자판기 배치.

실제 모약 자판기는 아직 시중에 없어서, 지도 화면 시연을 위해 DEMO_MAP_CENTER(발표 장소 근처) 주변에
가상 자판기를 배치한다. 지도 API(카카오/네이버)는 배경 지도만 그리고, 자판기 위치·재고는 이 데이터로 표시한다.

- M001은 키오스크 시연(/kiosk/...?machine=M001)에 쓰는 자판기와 같은 ID라, 앱에서 이 자판기를 고르면
  키오스크 QR 로그인 → 수령까지 같은 데이터로 이어진다.
- 이미 좌표가 있는 자판기는 덮어쓰지 않는다 (운영자가 직접 고친 위치 보존).
- 실제 자판기 데이터는 MAP_DATABASE_URL(Supabase)로 따로 읽으므로, 그 경우 이 가상 데이터는 쓰이지 않는다.
"""

import math

from sqlalchemy.orm import Session

from src import config
from src.consult import shop
from src.consult.models import Product, VendingMachine

# (id, 이름, 중심 기준 북쪽 m, 동쪽 m, 운영시간, 재고 배율) — 배율 0이면 전 품목 품절 시연용
DEMO_MACHINES = [
    ("M001", "모약 자판기 1호점", 180, 120, "24시간", 1.0),
    ("M002", "모약 자판기 2호점", -350, 420, "24시간", 0.5),
    ("M003", "모약 자판기 3호점", 620, -280, "06:00 ~ 24:00", 1.0),
    ("M004", "모약 자판기 4호점", -820, -540, "24시간", 0.2),
    ("M005", "모약 자판기 5호점", 1150, 760, "07:00 ~ 22:00", 0.0),
]

_M_PER_DEG_LAT = 111_320.0


def _center() -> tuple[float, float]:
    lat, lng = (float(v) for v in config.DEMO_MAP_CENTER.split(","))
    return lat, lng


def _offset(lat: float, lng: float, north_m: float, east_m: float) -> tuple[float, float]:
    return (
        lat + north_m / _M_PER_DEG_LAT,
        lng + east_m / (_M_PER_DEG_LAT * math.cos(math.radians(lat))),
    )


def seed_demo_machines(db: Session) -> None:
    center_lat, center_lng = _center()
    for machine_id, name, north_m, east_m, hours, stock_ratio in DEMO_MACHINES:
        machine = db.get(VendingMachine, machine_id)
        if machine is None:
            machine = VendingMachine(id=machine_id, name=name)
            db.add(machine)
        if machine.latitude is None or machine.longitude is None:
            machine.latitude, machine.longitude = _offset(center_lat, center_lng, north_m, east_m)
            machine.name = name
            machine.address = "시연용 가상 위치"
            machine.operating_hours = hours
            machine.is_active = True
        db.commit()

        # 재고가 아직 없는 자판기만 데모 카탈로그로 채우고, 자판기마다 재고량을 달리해 지도에서 차이가 보이게 한다
        if db.query(Product).filter(Product.machine_id == machine_id).first() is None:
            shop._seed_products_if_empty(db, machine_id)
            for product in db.query(Product).filter(Product.machine_id == machine_id):
                product.stock = int(product.stock * stock_ratio)
            db.commit()
