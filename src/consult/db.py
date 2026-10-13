"""약사 상담 + 자판기 연동용 DB 연결. RAG 파이프라인(Pinecone)과는 별개의 관계형 저장소."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from src import config
from src.map_database import create_database_engine

engine = create_database_engine(config.DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
# 로컬 SQLite만 우리가 구조를 관리한다. Postgres(팀 Supabase 공유 DB)는 구조를 바꾸지 않고 그대로 쓴다.
IS_SQLITE = engine.url.get_backend_name() == "sqlite"

Base = declarative_base()


def init_db() -> None:
    from src.consult import models  # noqa: F401  (모델 등록을 위해 임포트)

    if not IS_SQLITE:
        # 팀 공유 DB: 테이블 생성·컬럼 추가를 하지 않는다(구조 변경은 팀과 합의 후 SQL로).
        return
    _migrate_sqlite_to_shared_schema()
    Base.metadata.create_all(bind=engine)
    _add_missing_columns()


def _migrate_sqlite_to_shared_schema() -> None:
    """예전 로컬 SQLite를 팀 Supabase와 같은 구조로 맞춘다(2026-10-13 DB 통합).

    - vending_machines.id → code (자판기 코드 컬럼 이름)
    - consultation_messages: 문자열 id·content → 자동 증가 id·text·client_id (기존 메시지는 그대로 옮김)
    """
    insp = inspect(engine)
    with engine.begin() as conn:
        if insp.has_table("vending_machines"):
            cols = {c["name"] for c in insp.get_columns("vending_machines")}
            if "code" not in cols and "id" in cols:
                conn.execute(text("ALTER TABLE vending_machines RENAME COLUMN id TO code"))
        if insp.has_table("consultation_messages"):
            cols = {c["name"] for c in insp.get_columns("consultation_messages")}
            if "text" not in cols:
                conn.execute(text("ALTER TABLE consultation_messages RENAME TO consultation_messages_old"))
                conn.execute(text("DROP INDEX IF EXISTS ix_consultation_messages_consultation_id"))
    if insp.has_table("consultation_messages_old"):
        from src.consult.models import ConsultationMessage

        ConsultationMessage.__table__.create(bind=engine, checkfirst=True)
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO consultation_messages (consultation_id, sender_role, sender_id, client_id, text, created_at) "
                "SELECT consultation_id, sender_role, sender_id, id, content, created_at "
                "FROM consultation_messages_old ORDER BY created_at"))
            conn.execute(text("DROP TABLE consultation_messages_old"))


# create_all은 이미 있는 테이블에 새 컬럼을 추가하지 않는다. 로컬 SQLite를 지우지 않고도
# 새 컬럼이 반영되도록, 나중에 추가된 nullable 컬럼만 여기서 ALTER TABLE로 보강한다.
_LATE_COLUMNS = {
    "approved_purchases": {"price": "INTEGER", "paid_at": "DATETIME"},  # SQLite 전용(Postgres는 init_db에서 건너뜀)
    "consultation_requests": {"summary": "VARCHAR", "ended_at": "DATETIME"},
    "vending_machines": {
        "address": "VARCHAR",
        "latitude": "FLOAT",
        "longitude": "FLOAT",
        "operating_hours": "VARCHAR",
        "is_active": "BOOLEAN NOT NULL DEFAULT TRUE",
    },
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


def is_shared_db(db: Session) -> bool:
    """팀 공유 DB(Postgres)면 True — 자판기 자동 생성·데모 상품 채우기 같은 쓰기를 하지 않는다."""
    return db.get_bind().dialect.name != "sqlite"


def get_session() -> Session:
    return SessionLocal()


def get_db():
    """FastAPI 의존성 주입용 — 요청마다 세션을 만들고 끝나면 닫는다."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()