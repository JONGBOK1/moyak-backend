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


@lru_cache(maxsize=1)
def _load_drugs(csv_path: Path = CSV_PATH) -> list[DrugSummary]:
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return [
            DrugSummary(item_seq=row["item_seq"], item_name=row["item_name"], company=row["company"])
            for row in reader
        ]


def search_drugs(drugs: list[DrugSummary], q: str, limit: int = 20) -> list[DrugSummary]:
    query = q.strip().lower()
    if not query:
        return []
    return [d for d in drugs if query in d.item_name.lower()][:limit]


@router.get("/search", response_model=list[DrugSummary])
def search(q: str = Query(..., min_length=1), limit: int = 20) -> list[DrugSummary]:
    return search_drugs(_load_drugs(), q, limit)
