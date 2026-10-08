"""약사 상담 + 자판기 연동용 DB 연결. RAG 파이프라인(Pinecone)과는 별개의 관계형 저장소."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from src import config
from src.database.connection import create_database_engine

engine = create_database_engine(config.DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

Base = declarative_base()


def init_db() -> None:
    from src.consult import models  # noqa: F401  (모델 등록을 위해 임포트)

    if engine.dialect.name == "sqlite":
        _migrate_sqlite_vending_columns()
        Base.metadata.create_all(bind=engine)
        _migrate_sqlite_scenario_columns()
    else:
        # Shared databases are migrated explicitly, never mutated by API/worker startup.
        with engine.connect() as connection, connection.begin():
            connection.execute(text('SET TRANSACTION READ ONLY'))
            connection.execute(text("SET LOCAL statement_timeout='15s'"))
            problems = schema_problems(connection)
        if problems:
            raise RuntimeError('DB schema mapping/migration required: ' + '; '.join(problems))


def schema_problems(connection):
    """Validate table/column presence and identifier types; never run DDL."""
    from sqlalchemy import String, Integer
    inspector = inspect(connection)
    schema = 'public' if connection.dialect.name == 'postgresql' else None
    existing = set(inspector.get_table_names(schema=schema))
    problems = []
    for name, model in Base.metadata.tables.items():
        if name not in existing:
            problems.append('missing table ' + name)
            continue
        columns = {c['name']: c for c in inspector.get_columns(name, schema=schema)}
        for column in model.columns:
            actual = columns.get(column.name)
            label = name + '.' + column.name
            if actual is None:
                problems.append('missing column ' + label)
            elif column.primary_key or column.foreign_keys:
                if isinstance(column.type, String) and not isinstance(actual['type'], String):
                    problems.append('identifier type mismatch ' + label)
                elif isinstance(column.type, Integer) and not isinstance(actual['type'], Integer):
                    problems.append('identifier type mismatch ' + label)
    return problems


def _migrate_sqlite_vending_columns() -> None:
    """기존 로컬 SQLite DB에도 자판기 위치 컬럼을 추가한다(간단한 개발용 migration)."""
    inspector = inspect(engine)
    if "vending_machines" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("vending_machines")}
    additions = {
        "address": "TEXT NOT NULL DEFAULT ''",
        "operating_hours": "VARCHAR",
        "operating_hours": "VARCHAR",
        "latitude": "REAL",
        "longitude": "REAL",
        "is_active": "INTEGER NOT NULL DEFAULT 1",
    }
    with engine.begin() as connection:
        for name, definition in additions.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE vending_machines ADD COLUMN {name} {definition}"))


def _migrate_sqlite_scenario_columns():
    inspector = inspect(engine)
    additions = {
        "approved_purchases": {"price": "INTEGER", "paid_at": "DATETIME"},
        "vending_machines": {"paired_user_id": "VARCHAR", "paired_purchase_id": "VARCHAR"},
    }
    with engine.begin() as connection:
        for table, fields in additions.items():
            columns = {column["name"] for column in inspector.get_columns(table)}
            for name, definition in fields.items():
                if name not in columns:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))


def get_session() -> Session:
    return SessionLocal()


def get_db():
    """FastAPI 의존성 주입용 — 요청마다 세션을 만들고 끝나면 닫는다."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
