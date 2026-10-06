from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


def database_url(value):
    """Accept the PostgreSQL URI copied from Connect without exposing its secrets."""
    url = make_url(value)
    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+psycopg")
    if url.get_backend_name() not in {"sqlite", "postgresql"}:
        raise ValueError("Only SQLite and PostgreSQL are supported")
    if url.get_backend_name() == "postgresql":
        if url.drivername != "postgresql+psycopg":
            raise ValueError("Use postgresql+psycopg for the PostgreSQL driver")
        if not url.query.get("sslmode"):
            url = url.update_query_dict({"sslmode": "require"})
    return url


def create_database_engine(value):
    url = database_url(value)
    if url.get_backend_name() == "sqlite":
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(url, pool_pre_ping=True, pool_size=3, max_overflow=2,
                         hide_parameters=True, connect_args={"connect_timeout": 10, "prepare_threshold": None})
