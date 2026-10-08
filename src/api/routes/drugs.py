from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from src.drugs.database import get_catalog_db as get_db
from src.drugs import repository

router = APIRouter(prefix="/api/v1/drugs", tags=["drugs"])


def catalog_call(fn, *args):
    try:
        return fn(*args)
    except repository.CatalogNotConfigured as error:
        raise HTTPException(503, str(error)) from None


@router.get("")
def search(q: str | None = Query(None, min_length=1, max_length=100),
           limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=100000),
           db: Session = Depends(get_db)):
    return catalog_call(repository.list_drugs, db, q, limit, offset)


@router.get("/{item_seq}")
def detail(item_seq: str, db: Session = Depends(get_db)):
    row = catalog_call(repository.get_drug, db, item_seq)
    if row is None:
        raise HTTPException(404, "의약품을 찾을 수 없습니다.")
    return row


@router.get("/{item_seq}/{kind}")
def related(item_seq: str, kind: Literal["ingredients", "permissions", "pills"],
            limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=100000),
            db: Session = Depends(get_db)):
    return catalog_call(repository.related, db, item_seq, "drug_" + kind, limit, offset)
