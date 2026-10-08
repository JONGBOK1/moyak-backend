from sqlalchemy import create_engine, text
from src.database.connection import database_url
from scripts.inspect_database import inventory


def test_supabase_uri_uses_psycopg_and_tls_without_losing_escaped_password():
    url = database_url("postgresql://test:p%40ss@db.example:5432/postgres")
    assert url.drivername == "postgresql+psycopg"
    assert url.password == "p@ss"
    assert url.query["sslmode"] == "require"


def test_postgres_alias_and_existing_sslmode():
    url = database_url("postgres://test:password@db.example/postgres?sslmode=verify-full")
    assert url.drivername == "postgresql+psycopg"
    assert url.query["sslmode"] == "verify-full"


def test_read_only_inventory_reports_schema_not_user_records():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, private_name TEXT)"))
        conn.execute(text("INSERT INTO users VALUES (1, 'DO_NOT_EXPORT')"))
    result = inventory(engine)
    assert "users" in result["tables"]
    assert result["tables"]["users"]["primary_key"]["constrained_columns"] == ["id"]
    assert "DO_NOT_EXPORT" not in str(result)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM users")).scalar_one() == 1
    engine.dispose()


def test_postgresql_startup_never_creates_tables(monkeypatch):
    from src.consult import db
    import pytest
    statements = []
    class FakeEngine:
        class dialect:
            name = "postgresql"
        def connect(self):
            return self
        def begin(self):
            return self
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def execute(self, statement):
            sql = str(statement)
            assert sql.startswith('SET '), 'Startup attempted a database mutation'
            statements.append(sql)
    class Inspector:
        def get_table_names(self, schema):
            return []
    monkeypatch.setattr(db, "engine", FakeEngine())
    monkeypatch.setattr(db, "inspect", lambda engine: Inspector())
    monkeypatch.setattr(db.Base.metadata, "create_all", lambda **kwargs: pytest.fail("Unexpected schema mutation"))
    with pytest.raises(RuntimeError, match="mapping/migration required"):
        db.init_db()
    assert statements[0] == 'SET TRANSACTION READ ONLY'
