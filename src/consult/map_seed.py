"""지도 시연용 가상 자판기 배치 (동양미래대학교 주변 실제 장소).

실제 모약 자판기는 아직 시중에 없어서, 지도 화면 시연을 위해 가상 자판기를 배치한다.
지도 API(카카오)는 배경 지도만 그리고, 자판기 위치·재고는 이 데이터로 표시한다.
같은 자판기를 Supabase에 넣는 SQL은 scripts/supabase_demo_machines.sql (MAP_DATABASE_URL을 쓰면 그쪽이 표시됨).

- M001은 키오스크 시연(/kiosk/...?machine=M001)에 쓰는 자판기와 같은 ID라, 앱에서 이 자판기를 고르면
  키오스크 QR 로그인 → 수령까지 같은 데이터로 이어진다.
- 운영자가 직접 고친 위치는 덮어쓰지 않는다 (좌표가 없거나 예전 시드 값일 때만 채움).
"""

from sqlalchemy.orm import Session

from src.consult import shop
from src.consult.models import Product, VendingMachine

# (id, 이름, 주소, 위도, 경도, 운영시간, 재고 배율) — 배율 0이면 전 품목 품절 시연용
# 좌표: OpenStreetMap 기준 각 장소 위치
DEMO_MACHINES = [
    ("M001", "모약 자판기 동양미래대학교점", "서울 구로구 경인로 445 동양미래대학교", 37.5011, 126.8670, "24시간", 1.0),
    ("M002", "모약 자판기 고척스카이돔점", "서울 구로구 경인로 430 고척스카이돔", 37.4982, 126.8671, "24시간", 0.5),
    ("M003", "모약 자판기 구일역점", "서울 구로구 구일로 133 구일역", 37.4964, 126.8709, "05:30 ~ 24:00", 1.0),
    ("M004", "모약 자판기 개봉역점", "서울 구로구 개봉동 개봉역 인근", 37.4952, 126.8587, "24시간", 0.2),
    ("M005", "모약 자판기 구로구청점", "서울 구로구 가마산로 구로구청 인근", 37.4947, 126.8876, "09:00 ~ 18:00", 0.0),
]

_OLD_PLACEHOLDER_ADDRESS = "시연용 가상 위치"  # 예전 버전(서울시청 기준)이 넣은 값 — 새 위치로 교체 대상


def seed_demo_machines(db: Session) -> None:
    for machine_id, name, address, lat, lng, hours, stock_ratio in DEMO_MACHINES:
        machine = db.get(VendingMachine, machine_id)
        if machine is None:
            machine = VendingMachine(id=machine_id, name=name)
            db.add(machine)
        if machine.latitude is None or machine.address in (None, _OLD_PLACEHOLDER_ADDRESS):
            machine.name = name
            machine.address = address
            machine.latitude, machine.longitude = lat, lng
            machine.operating_hours = hours
            machine.is_active = True
        db.commit()

        # 재고가 아직 없는 자판기만 데모 카탈로그로 채우고, 자판기마다 재고량을 달리해 지도에서 차이가 보이게 한다
        if db.query(Product).filter(Product.machine_id == machine_id).first() is None:
            shop._seed_products_if_empty(db, machine_id)
            for product in db.query(Product).filter(Product.machine_id == machine_id):
                product.stock = int(product.stock * stock_ratio)
            db.commit()
