"""약사 대시보드에서 처방할 약품을 검색할 때 쓰는 자동완성 API."""

from fastapi import APIRouter, Query, Depends, HTTPException
from sqlalchemy.orm import Session
from src.drugs.database import get_catalog_db
from src.drugs import repository
from pydantic import BaseModel

router = APIRouter(prefix="/drugs", tags=["drugs"])



class DrugSummary(BaseModel):
    item_seq: str
    item_name: str
    company: str


@router.get("/search", response_model=list[DrugSummary])
def search(q: str = Query(..., min_length=1, max_length=100),
           limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_catalog_db)):
    if not q.strip():
        return []
    try:
        rows = repository.list_drugs(db, q.strip(), limit)['items']
    except repository.CatalogNotConfigured as error:
        raise HTTPException(503, str(error)) from None
    return [DrugSummary(item_seq=row['item_seq'], item_name=row['item_name'],
                        company=row.get('entp_name') or '') for row in rows]
