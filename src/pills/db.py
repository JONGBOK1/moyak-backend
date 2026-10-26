"""알약 검색이 쓸 Supabase 연결 (읽기 전용).

팀 공용 Supabase 는 절대 수정하지 않는다 — 지도·DUR 보강과 같은 원칙이라
모든 조회를 `SET TRANSACTION READ ONLY` 를 건 트랜잭션에서 한다.

API 라우터와 측정 스크립트(`scripts/check_pill_*.py`)가 같은 연결을 쓴다.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import text

from src import config

NO_DB = "알약 검색 DB가 설정되지 않았습니다. PILL_DATABASE_URL(또는 MAP_DATABASE_URL)을 확인해주세요."


def database_url() -> str | None:
    """붙여넣을 때 끝에 줄바꿈/공백이 섞이는 일이 잦아 정리한다(map.py 와 같은 이유)."""
    for value in (config.PILL_DATABASE_URL,
                  os.getenv("DRUG_DATABASE_URL"),
                  os.getenv("MAP_DATABASE_URL")):
        url = (value or "").strip()
        if url:
            return url
    return None


@lru_cache(maxsize=1)
def engine():
    """주소가 없으면 None. 부르는 쪽에서 안내 문구를 내보낸다."""
    url = database_url()
    if not url:
        return None
    from src.map_database import create_database_engine

    return create_database_engine(url)


@contextmanager
def read_only(timeout: str = "10s"):
    """읽기 전용 커넥션. 측정 스크립트처럼 오래 걸리는 쪽은 timeout 을 늘려 쓴다."""
    eng = engine()
    if eng is None:
        raise RuntimeError(NO_DB)
    with eng.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        conn.execute(text(f"SET LOCAL statement_timeout = '{timeout}'"))
        yield conn
