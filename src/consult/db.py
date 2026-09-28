"""약사 상담 + 자판기 연동용 DB 연결. RAG 파이프라인(Pinecone)과는 별개의 관계형 저장소."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from src import config

connect_args = {"check_same_thread": False} if config.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(config.DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

Base = declarative_base()


def init_db() -> None:
    from src.consult import models  # noqa: F401  (모델 등록을 위해 임포트)

    Base.metadata.create_all(bind=engine)
    _add_missing_columns()


# create_all은 이미 있는 테이블에 새 컬럼을 추가하지 않는다. 로컬 SQLite를 지우지 않고도
# 새 컬럼이 반영되도록, 나중에 추가된 nullable 컬럼만 여기서 ALTER TABLE로 보강한다.
_LATE_COLUMNS = {
    "approved_purchases": {"price": "INTEGER", "paid_at": "DATETIME"},
}


def _add_missing_columns() -> None:
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, columns in _LATE_COLUMNS.items():
            if not insp.has_table(table):
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for name, sql_type in columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))


def get_session() -> Session:
    return SessionLocal()


def get_db():
    """FastAPI 의존성 주입용 — 요청마다 세션을 만들고 끝나면 닫는다."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()