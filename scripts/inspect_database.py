"""Read-only schema inventory; excludes data rows, credentials and default values.

Does not import application startup or call create_all. PostgreSQL statements run
in a READ ONLY transaction. Keep resulting schema files local by default.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from sqlalchemy import inspect, text
from src.database.connection import create_database_engine


def inventory(engine):
    with engine.connect() as conn, conn.begin():
        postgres = engine.dialect.name == "postgresql"
        if postgres:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            conn.execute(text("SET LOCAL statement_timeout = '15s'"))
        inspector = inspect(conn)
        schema = "public" if postgres else None
        result = {"database_type": engine.dialect.name, "tables": {}}
        for name in inspector.get_table_names(schema=schema):
            if name == "spatial_ref_sys":
                continue
            result["tables"][name] = {
                "columns": [{"name": col["name"], "type": str(col["type"]),
                             "nullable": col["nullable"], "has_default": col.get("default") is not None,
                             "identity": bool(col.get("identity"))}
                            for col in inspector.get_columns(name, schema=schema)],
                "primary_key": inspector.get_pk_constraint(name, schema=schema),
                "foreign_keys": inspector.get_foreign_keys(name, schema=schema),
                "unique_constraints": inspector.get_unique_constraints(name, schema=schema),
                "indexes": inspector.get_indexes(name, schema=schema),
                "checks": inspector.get_check_constraints(name, schema=schema),
            }
        if postgres:
            result["row_security"] = [dict(r) for r in conn.execute(text(
                "SELECT c.relname AS table_name, c.relrowsecurity AS enabled, c.relforcerowsecurity AS forced "
                "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relkind='r' ORDER BY c.relname")).mappings()]
            result["policies"] = [dict(r) for r in conn.execute(text(
                "SELECT tablename, policyname, permissive, roles, cmd, qual, with_check "
                "FROM pg_policies WHERE schemaname='public' ORDER BY tablename, policyname")).mappings()]
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(ROOT / "data" / "database-schema.json"))
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    value = os.getenv("DATABASE_URL")
    if not value:
        print("DATABASE_URL is not configured in .env. No connection attempted.")
        return 2
    engine = None
    try:
        engine = create_database_engine(value)
        result = inventory(engine)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"Read-only inspection complete: {len(result['tables'])} tables. Report: {output}")
        return 0
    except Exception as error:
        # Connection errors can embed DSNs. Never print the exception message.
        print(f"Inspection failed ({type(error).__name__}). Check connection settings, credentials and network.")
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
