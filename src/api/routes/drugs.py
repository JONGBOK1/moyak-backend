"""약사 대시보드에서 처방할 약품을 검색할 때 쓰는 자동완성 API."""

import csv
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Query
from pydantic import BaseModel

from src.config import DATA_PROCESSED_DIR

router = APIRouter(prefix="/drugs", tags=["drugs"])

CSV_PATH = DATA_PROCESSED_DIR / "eyakeunyo_clean.csv"


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
