"""약사 대시보드에서 처방할 약품을 검색할 때 쓰는 자동완성 API."""

import csv
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter(prefix="/drugs", tags=["drugs"])

# data/processed/는 .gitignore라 배포 서버에 없다 — 검색용 3개 컬럼만 뽑아 git에 포함시킨
# 경량 인덱스를 쓴다 (scripts/build_drug_index.py로 생성).
CSV_PATH = Path(__file__).resolve().parent.parent / "drug_index.csv"


class DrugSummary(BaseModel):
    item_seq: str
    item_name: str
    company: str


#: **전문의약품은 약사가 처방 목록에서 고를 수 없어야 한다.**
#: 자판기 판매는 의사 진단이 필요 없는 약만 다룬다는 게 이 프로젝트의 전제인데,
#: 색인에 전문의약품 9종(습진 스테로이드 연고 6 · 녹내장·결막염 점안액 3)이 섞여 있어
#: 자동완성으로 고를 수 있었다. 키오스크를 지정한 상담은 재고 목록에서만 고르게 돼 있어
#: 막혔지만, 키오스크를 안 고른 상담은 이 검색이 그대로 열려 있었다(한별 확인 요청, 2026-10-27).
#: 색인의 `etc_otc` 칸은 식약처 허가정보에서 온다(scripts/build_drug_index.py).
PRESCRIPTION_ONLY = "전문의약품"


@lru_cache(maxsize=1)
def _load_drugs(csv_path: Path = CSV_PATH) -> list[DrugSummary]:
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return [
            DrugSummary(item_seq=row["item_seq"], item_name=row["item_name"], company=row["company"])
            for row in reader
            #: 칸이 없는 예전 색인이면 걸러내지 못한다 — 그때는 예전처럼 전부 보여준다.
            #: (칸을 못 읽었다고 4,757종을 통째로 숨기면 처방 자체를 못 하게 된다)
            if (row.get("etc_otc") or "") != PRESCRIPTION_ONLY
        ]


def search_drugs(drugs: list[DrugSummary], q: str, limit: int = 20) -> list[DrugSummary]:
    query = q.strip().lower()
    if not query:
        return []
    return [d for d in drugs if query in d.item_name.lower()][:limit]


@router.get("/search", response_model=list[DrugSummary])
def search(q: str = Query(..., min_length=1), limit: int = 20) -> list[DrugSummary]:
    return search_drugs(_load_drugs(), q, limit)
