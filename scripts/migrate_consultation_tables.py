"""Create only the four conversation tables; existing tables/data are untouched.

Default: inspect and report. --apply: transactional creation with RLS enabled.
Direct anon/authenticated Data API access is disabled; use authenticated backend APIs.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import MetaData, inspect, text
from src.consult.db import engine
from src.consult.models import (ConsultationRequest, ConsultationSession,
                                ConsultationMessage, ConsultationAudio, ConsultationSummary)

MODELS = (ConsultationSession, ConsultationMessage, ConsultationAudio, ConsultationSummary)


def verify(conn, tables):
    inspector = inspect(conn)
    for table in tables:
        columns = {c['name']: c for c in inspector.get_columns(table.name, schema='public')}
        for column in table.columns:
            actual = columns.get(column.name)
            if actual is None or actual['nullable'] != column.nullable:
                raise RuntimeError(f"Column mismatch: {table.name}.{column.name}")
            expected_type = column.type.compile(dialect=conn.dialect)
            actual_type = actual['type'].compile(dialect=conn.dialect)
            if actual_type != expected_type:
                raise RuntimeError(f"Type mismatch: {table.name}.{column.name}")
        pk = inspector.get_pk_constraint(table.name, schema='public')['constrained_columns']
        if pk != [c.name for c in table.primary_key]:
            raise RuntimeError(f"Primary key mismatch: {table.name}")
        fks = inspector.get_foreign_keys(table.name, schema='public')
        if not any(f['constrained_columns'] == ['consultation_id'] and
                   f['referred_table'] == 'consultation_requests' and f['referred_columns'] == ['id'] for f in fks):
            raise RuntimeError(f"Foreign key mismatch: {table.name}")
        if table.name in {'consultation_messages', 'consultation_audio'}:
            uniques = inspector.get_unique_constraints(table.name, schema='public')
            if not any(u['column_names'] == ['consultation_id', 'sender_role', 'sender_id', 'client_id'] for u in uniques):
                raise RuntimeError(f"Deduplication constraint missing: {table.name}")
        secured = conn.execute(text(
            "SELECT c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relname=:name"), {'name': table.name}).scalar_one()
        if not secured:
            raise RuntimeError(f"RLS is disabled: {table.name}")


def main(apply=False):
    if engine.dialect.name != 'postgresql':
        raise RuntimeError('A PostgreSQL DATABASE_URL is required')
    metadata = MetaData(schema='public')
    ConsultationRequest.__table__.to_metadata(metadata, schema='public')
    tables = [model.__table__.to_metadata(metadata, schema='public') for model in MODELS]
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL lock_timeout = '5s'"))
        conn.execute(text("SET LOCAL statement_timeout = '30s'"))
        if apply:
            conn.execute(text("SELECT pg_advisory_xact_lock(74926105)"))
        else:
            conn.execute(text("SET TRANSACTION READ ONLY"))
        inspector = inspect(conn)
        existing = set(inspector.get_table_names(schema='public'))
        parent = inspector.get_columns('consultation_requests', schema='public')
        if not any(c['name'] == 'id' and str(c['type']).startswith('VARCHAR') for c in parent):
            raise RuntimeError('Unexpected consultation_requests.id type')
        missing = [t for t in tables if t.name not in existing]
        print('Missing tables: ' + (', '.join(t.name for t in missing) or 'none'))
        if not apply and missing:
            print('Dry run only; no database changes.')
            return
        for table in missing:
            table.create(conn)
            qualified = 'public.' + table.name  # fixed model names, not user input
            conn.execute(text(f'ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY'))
            conn.execute(text(f'REVOKE ALL ON TABLE {qualified} FROM PUBLIC, anon, authenticated'))
            if 'id' in table.c:
                sequence = f'public.{table.name}_id_seq'
                conn.execute(text(f'REVOKE ALL ON SEQUENCE {sequence} FROM PUBLIC, anon, authenticated'))
        verify(conn, tables)
        print('Verified: columns/types, primary/foreign keys, deduplication and RLS for all four tables.')
    print('Migration committed.' if apply else 'Read-only verification complete.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        main(args.apply)
    except Exception as error:
        print(f'Migration failed ({type(error).__name__}); transaction rolled back. No credentials logged.')
        raise SystemExit(1)
    finally:
        engine.dispose()
