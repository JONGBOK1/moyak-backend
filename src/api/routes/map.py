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
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from pathlib import Path
from dotenv import load_dotenv
from src import config
from src.map_database import create_database_engine

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
    url = _map_database_url()
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
        if engine.dialect.name == "postgresql" and _map_database_url():
            _begin_read_only(conn)
            extra = "AND m.id::text = :machine_id" if machine_id else ""
            rows = conn.execute(text(_SUPABASE_MACHINES.format(extra=extra)), params).mappings().all()
            return "supabase", rows
        extra = "AND m.id = :machine_id" if machine_id else ""
        rows = conn.execute(text(_LOCAL_MACHINES.format(extra=extra)), params).mappings().all()
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
    engine=Depends(map_engine),
):
    if (lat is None) != (lng is None):
        raise HTTPException(422, "lat과 lng는 함께 보내야 합니다.")
    try:
        source, rows = _query_machines(engine)
    except SQLAlchemyError as e:
        raise _db_unavailable(e) from None
    items = [_with_distance(row, lat, lng) for row in rows[:MAX_MACHINES]]
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
    try:
        source, rows = _query_machines(engine, machine_id)
        if not rows:
            raise HTTPException(404, "자판기를 찾을 수 없습니다.")
        with engine.connect() as conn, conn.begin():
            if source == "supabase":
                _begin_read_only(conn)
                inventory = conn.execute(
                    text("SELECT i.item_seq, i.stock, i.price FROM public.machine_inventory i "
                         "WHERE i.machine_id::text = :machine_id ORDER BY i.item_seq"),
                    {"machine_id": machine_id},
                ).mappings().all()
                names = _drug_names()
                items = [
                    {"item_seq": r["item_seq"], "item_name": names.get(str(r["item_seq"]), str(r["item_seq"])),
                     "stock": r["stock"], "price": int(r["price"]) if r["price"] is not None else None, "category": None}
                    for r in inventory
                ]
            else:
                inventory = conn.execute(
                    text("SELECT item_seq, item_name, stock, price, category FROM products "
                         "WHERE machine_id = :machine_id ORDER BY category, item_name"),
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
    orig = getattr(e, "orig", None) or e
    logger.warning("map DB error: %s: %s", type(orig).__name__, str(orig).splitlines()[0][:300] if str(orig) else "")
    return HTTPException(503, f"{DB_ERROR} ({type(orig).__name__})")


def _drug_names() -> dict[str, str]:
    """Supabase 재고는 품목기준코드(item_seq)만 있어서, 약사 검색용 약품 인덱스로 이름을 붙인다."""
    from src.api.routes.drugs import _load_drugs

    return {d.item_seq: d.item_name for d in _load_drugs()}
