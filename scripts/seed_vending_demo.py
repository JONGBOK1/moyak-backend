"""로컬 API 확인용 자판기/재고 샘플 데이터 삽입 스크립트."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.consult.db import get_session, init_db
from src.vending import repository


def main() -> None:
    init_db()
    db = get_session()
    try:
        repository.upsert_machine(
            db,
            {
                "id": "vm-gangnam",
                "name": "강남역 모약이 자판기",
                "address": "서울특별시 강남구 강남대로 396",
                "latitude": 37.4979,
                "longitude": 127.0276,
                "is_active": True,
            },
        )
        repository.upsert_inventory(
            db,
            "vm-gangnam",
            {"drug_id": "drug-tylenol", "drug_name": "타이레놀정", "category": "해열진통제", "price": 3000, "stock_count": 12},
        )
        repository.upsert_inventory(
            db,
            "vm-gangnam",
            {"drug_id": "drug-ointment", "drug_name": "상처연고", "category": "연고", "price": 4500, "stock_count": 0},
        )
        repository.upsert_machine(
            db,
            {
                "id": "vm-seolleung",
                "name": "선릉역 모약이 자판기",
                "address": "서울특별시 강남구 테헤란로 212",
                "latitude": 37.5045,
                "longitude": 127.0489,
                "is_active": True,
            },
        )
        repository.upsert_inventory(
            db,
            "vm-seolleung",
            {"drug_id": "drug-cold", "drug_name": "감기약", "category": "감기약", "price": 3500, "stock_count": 7},
        )
        repository.upsert_machine(
            db,
            {
                "id": "vm-samseong",
                "name": "삼성역 모약이 자판기",
                "address": "서울특별시 강남구 영동대로 513",
                "latitude": 37.5112,
                "longitude": 127.0591,
                "is_active": False,
            },
        )
        repository.upsert_inventory(
            db,
            "vm-samseong",
            {"drug_id": "drug-bandage", "drug_name": "밴드", "category": "위생용품", "price": 1500, "stock_count": 18},
        )
        print("Demo vending machine data inserted.")
    finally:
        db.close()


if __name__ == "__main__":
    main()