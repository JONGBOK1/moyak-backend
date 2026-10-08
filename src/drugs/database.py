"""Read-only catalog database connection."""
import os
from functools import lru_cache
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from src import config
from src.database.connection import create_database_engine

@lru_cache(maxsize=1)
def catalog_engine():
    return create_database_engine(os.getenv('CATALOG_DATABASE_URL') or config.DATABASE_URL)

def get_catalog_db():
    try:
        with Session(catalog_engine()) as db:
            if db.bind.dialect.name == 'postgresql':
                db.execute(text('SET TRANSACTION READ ONLY'))
                db.execute(text("SET LOCAL statement_timeout = '10s'"))
            yield db
    except SQLAlchemyError:
        raise HTTPException(503, '의약품 DB에 연결하지 못했습니다. 연결 설정을 확인해주세요.') from None
