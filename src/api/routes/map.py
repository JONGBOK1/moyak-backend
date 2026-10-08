"""Public map data only; every PostgreSQL query runs in a read-only transaction.

데이터 출처 두 가지 (응답의 `source`로 구분):
- `supabase`: MAP_DATABASE_URL이 있으면 팀 Supabase(PostGIS)의 실제 자판기 위치/재고를 읽기 전용으로 조회
  (`vending_machines.location`, `machine_inventory.stock`).
- `local`: 없으면 이 서버의 DB(config.DATABASE_URL)에서 읽는다 — 시연용 가상 자판기(src/consult/map_seed.py)와
  키오스크가 쓰는 의약외품 재고(`products`)가 그대로 지도에 나온다.

개인정보·QR 토큰·DB 비밀번호는 응답에 포함하지 않는다.
"""
import logging
import math
import os
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text, inspect
from sqlalchemy.exc import SQLAlchemyError

from pathlib import Path
from dotenv import load_dotenv
from src import config
from src.database.connection import create_database_engine

load_dotenv(Path(__file__).resolve().parents[3] / ".env")

router = APIRouter(prefix="/api/v1/map", tags=["map"])
logger = logging.getLogger("uvicorn.error")

MAX_MACHINES = 500
DB_ERROR = "자판기 위치를 불러오지 못했습니다. DB 연결과 위치 정보를 확인해주세요."


def _map_database_url() -> str | None:
    # 대시보드에 붙여넣을 때 끝에 줄바꿈/공백이 섞이면 DB 이름 뒤에 줄바꿈이 붙어 연결이 실패하므로 정리한다
    return (os.getenv("MAP_DATABASE_URL") or "").strip() or None


@lru_cache(maxsize=1)
def map_engine():
    url = _map_database_url() or config.DATABASE_URL
    if url:
        return create_database_engine(url)
    # Supabase 미설정 시 앱 자체 DB(시연용 가상 자판기 + 키오스크 재고)를 쓴다
    from src.consult.db import engine

    return engine


def _distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """두 좌표 사이 직선거리(m, 하버사인)."""
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _begin_read_only(conn) -> None:
    conn.execute(text("SET TRANSACTION READ ONLY"))
    conn.execute(text("SET LOCAL statement_timeout = '10s'"))
    conn.execute(text("SET LOCAL search_path = public, extensions"))


_SUPABASE_MACHINES = """
    SELECT m.id::text AS id, m.code, m.name, COALESCE(m.address, '') AS address,
           ST_Y(m.location::geometry) AS latitude,
           ST_X(m.location::geometry) AS longitude,
           m.operating_hours,
           COUNT(i.item_seq) AS item_count,
           COALESCE(SUM(i.stock), 0) AS stock_count
    FROM public.vending_machines m
    LEFT JOIN public.machine_inventory i ON i.machine_id = m.id
    WHERE m.is_active = true AND m.location IS NOT NULL {extra}
    GROUP BY m.id ORDER BY m.name, m.id LIMIT 501
"""

_LOCAL_MACHINES = """
    SELECT m.id, m.id AS code, m.name, COALESCE(m.address, '') AS address,
           m.latitude, m.longitude, m.operating_hours,
           COUNT(p.id) AS item_count, COALESCE(SUM(p.stock), 0) AS stock_count
    FROM vending_machines m
    LEFT JOIN products p ON p.machine_id = m.id
    WHERE m.is_active AND m.latitude IS NOT NULL AND m.longitude IS NOT NULL {extra}
    GROUP BY m.id ORDER BY m.name, m.id LIMIT 501
"""


def _query_machines(engine, machine_id: str | None = None):
    params = {"machine_id": machine_id} if machine_id else {}
    with engine.connect() as conn, conn.begin():
        if engine.dialect.name == "postgresql":
            _begin_read_only(conn)
            extra = "AND m.id::text = :machine_id" if machine_id else ""
            rows = conn.execute(text(_SUPABASE_MACHINES.format(extra=extra)), params).mappings().all()
            return "supabase", rows
        extra = "AND m.id = :machine_id" if machine_id else ""
        columns = {c['name'] for c in inspect(conn).get_columns('vending_machines')}
        hours = 'm.operating_hours' if 'operating_hours' in columns else 'NULL'
        inventory = _local_inventory_sql(conn)
        rows = conn.execute(text(f"""
          SELECT m.id,m.id AS code,m.name,COALESCE(m.address,'') AS address,
                 m.latitude,m.longitude,{hours} AS operating_hours,
                 COUNT(p.item_seq) AS item_count,COALESCE(SUM(p.stock),0) AS stock_count
          FROM vending_machines m LEFT JOIN ({inventory}) p ON p.machine_id=m.id
          WHERE m.is_active AND m.latitude IS NOT NULL AND m.longitude IS NOT NULL {extra}
          GROUP BY m.id ORDER BY m.name,m.id LIMIT 501
        """), params).mappings().all()
        return "local", rows


def _with_distance(row, lat: float | None, lng: float | None) -> dict:
    item = dict(row)
    item["distance_m"] = (
        round(_distance_m(lat, lng, item["latitude"], item["longitude"])) if lat is not None and lng is not None else None
    )
    return item


@router.get("/config")
def map_config():
    """지도 화면 초기화용 — 카카오 지도 JS 키(공개 키, 도메인 제한)와 기본 중심 좌표(동양미래대학교)."""
    lat, lng = (float(v) for v in config.DEMO_MAP_CENTER.split(","))
    return {"kakao_js_key": config.KAKAO_JS_KEY or None, "default_center": {"latitude": lat, "longitude": lng}}


@router.get("/machines")
def machines(
    lat: float | None = Query(None, ge=-90, le=90, description="내 위치 위도 — 주면 가까운 순 정렬 + distance_m"),
    lng: float | None = Query(None, ge=-180, le=180, description="내 위치 경도"),
    area_radius_m: int | None = Query(
        None, gt=0, le=50_000,
        description="지정하면 서비스 지역 중심(DEMO_MAP_CENTER, 기본 동양미래대) 반경 안의 자판기만 반환 — 시연 지역 한정용",
    ),
    engine=Depends(map_engine),
):
    if (lat is None) != (lng is None):
        raise HTTPException(422, "lat과 lng는 함께 보내야 합니다.")
    try:
        source, rows = _query_machines(engine)
    except SQLAlchemyError as e:
        raise _db_unavailable(e) from None
    items = [_with_distance(row, lat, lng) for row in rows[:MAX_MACHINES]]
    if area_radius_m:
        c_lat, c_lng = (float(v) for v in config.DEMO_MAP_CENTER.split(","))
        items = [m for m in items if _distance_m(c_lat, c_lng, m["latitude"], m["longitude"]) <= area_radius_m]
    if lat is not None:
        items.sort(key=lambda m: m["distance_m"])
    return {"source": source, "truncated": len(rows) > MAX_MACHINES, "items": items}


@router.get("/machines/{machine_id}")
def machine_detail(
    machine_id: str,
    lat: float | None = Query(None, ge=-90, le=90),
    lng: float | None = Query(None, ge=-180, le=180),
    engine=Depends(map_engine),
):
    """자판기 하나의 위치 + 품목별 재고 — 지도에서 마커를 눌렀을 때 쓴다."""
    if (lat is None) != (lng is None):
        raise HTTPException(422, 'lat과 lng는 함께 보내야 합니다.')
    try:
        source, rows = _query_machines(engine, machine_id)
        if not rows:
            raise HTTPException(404, "자판기를 찾을 수 없습니다.")
        with engine.connect() as conn, conn.begin():
            if source == "supabase":
                _begin_read_only(conn)
                inventory = conn.execute(
                    text("SELECT i.item_seq, i.stock, i.price, COALESCE(d.item_name,p.item_name,i.item_seq) AS item_name "
                         "FROM public.machine_inventory i LEFT JOIN public.drugs d ON d.item_seq=i.item_seq "
                         "LEFT JOIN LATERAL (SELECT item_name FROM public.drug_permissions "
                         "WHERE item_seq=i.item_seq ORDER BY item_name LIMIT 1) p ON true "
                         "WHERE i.machine_id::text = :machine_id ORDER BY i.item_seq"),
                    {"machine_id": machine_id},
                ).mappings().all()
                items = [
                    {"item_seq": r["item_seq"], "item_name": r['item_name'],
                     "stock": r["stock"], "price": int(r["price"]) if r["price"] is not None else None, "category": None}
                    for r in inventory
                ]
            else:
                inventory = conn.execute(
                    text("SELECT item_seq, item_name, stock, price, category FROM (" + _local_inventory_sql(conn) +
                         ") i WHERE machine_id = :machine_id ORDER BY category, item_name"),
                    {"machine_id": machine_id},
                ).mappings().all()
                items = [dict(r) for r in inventory]
    except SQLAlchemyError as e:
        raise _db_unavailable(e) from None
    machine = _with_distance(rows[0], lat, lng)
    machine["items"] = items
    machine["source"] = source
    return machine


def _db_unavailable(e: SQLAlchemyError) -> HTTPException:
    # 원인 파악용으로 서버 로그엔 오류 종류/메시지를 남기고(엔진이 hide_parameters라 비밀번호·쿼리 값은 안 나옴),
    # 응답엔 오류 종류 이름만 준다 — DB 주소·비밀번호·SQL은 절대 응답에 넣지 않는다.
    logger.warning('map DB error: %s', type(e).__name__)
    return HTTPException(503, DB_ERROR)


def _local_inventory_sql(conn):
    tables = set(inspect(conn).get_table_names())
    parts = []
    if 'products' in tables:
        parts.append('SELECT machine_id,item_seq,item_name,stock,price,category FROM products')
    if 'vending_inventory' in tables:
        cols = {c['name'] for c in inspect(conn).get_columns('vending_inventory')}
        seq = 'drug_id' if 'drug_id' in cols else 'CAST(id AS TEXT)'
        name = 'drug_name' if 'drug_name' in cols else seq
        price = 'price' if 'price' in cols else 'NULL'
        category = 'category' if 'category' in cols else 'NULL'
        exclude = (' WHERE NOT EXISTS (SELECT 1 FROM products p WHERE p.machine_id=v.machine_id '
                   'AND p.item_seq=v.drug_id)') if 'products' in tables and 'drug_id' in cols else ''
        parts.append(f'SELECT machine_id,{seq} AS item_seq,{name} AS item_name,stock_count AS stock,'
                     f'{price} AS price,{category} AS category FROM vending_inventory v' + exclude)
    return ' UNION ALL '.join(parts) if parts else (
        'SELECT NULL AS machine_id,NULL AS item_seq,NULL AS item_name,0 AS stock,NULL AS price,NULL AS category WHERE 0=1')
