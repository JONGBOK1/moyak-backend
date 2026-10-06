"""Public map data only; every PostgreSQL query runs in a read-only transaction."""
import os
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src import config
from src.database.connection import create_database_engine

router = APIRouter(prefix="/api/v1/map", tags=["map"])


@lru_cache(maxsize=1)
def map_engine():
    url = os.getenv("MAP_DATABASE_URL") or config.DATABASE_URL
    return create_database_engine(url)


@router.get("/machines")
def machines(engine=Depends(map_engine)):
    try:
        with engine.connect() as conn, conn.begin():
            if engine.dialect.name == "postgresql":
                conn.execute(text("SET TRANSACTION READ ONLY"))
                conn.execute(text("SET LOCAL statement_timeout = '10s'"))
                conn.execute(text("SET LOCAL search_path = public, extensions"))
                rows = conn.execute(text("""
                    SELECT m.id::text AS id, m.name, COALESCE(m.address, '') AS address,
                           ST_Y(m.location::geometry) AS latitude,
                           ST_X(m.location::geometry) AS longitude,
                           m.operating_hours,
                           COUNT(i.item_seq) AS item_count,
                           COALESCE(SUM(i.stock), 0) AS stock_count
                    FROM public.vending_machines m
                    LEFT JOIN public.machine_inventory i ON i.machine_id = m.id
                    WHERE m.is_active = true AND m.location IS NOT NULL
                    GROUP BY m.id ORDER BY m.name, m.id LIMIT 501
                """)).mappings().all()
                source = "supabase"
            else:
                rows = conn.execute(text("""
                    SELECT m.id, m.name, COALESCE(m.address, '') AS address,
                           m.latitude, m.longitude, NULL AS operating_hours,
                           COUNT(i.id) AS item_count, COALESCE(SUM(i.stock_count), 0) AS stock_count
                    FROM vending_machines m
                    LEFT JOIN vending_inventory i ON i.machine_id = m.id
                    WHERE m.is_active = 1 AND m.latitude IS NOT NULL AND m.longitude IS NOT NULL
                    GROUP BY m.id ORDER BY m.name, m.id LIMIT 501
                """)).mappings().all()
                source = "local"
        return {"source": source, "truncated": len(rows) > 500,
                "items": [dict(row) for row in rows[:500]]}
    except SQLAlchemyError:
        raise HTTPException(503, "자판기 위치를 불러오지 못했습니다. DB 연결과 위치 정보를 확인해주세요.") from None
