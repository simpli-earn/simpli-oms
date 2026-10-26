from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, URL, make_url
from sqlalchemy.orm import sessionmaker


def _database_url(value: str) -> URL:
    """Use psycopg 3 and require TLS for every Postgres connection."""
    if value.startswith("postgres://"):
        value = "postgresql://" + value.removeprefix("postgres://")
    url = make_url(value)
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    if url.drivername.startswith("postgresql") and "sslmode" not in url.query:
        url = url.update_query_dict({"sslmode": "require"})
    return url


def make_engine(url: str, pool_size: int = 2, max_overflow: int = 1) -> Engine:
    if url.startswith("sqlite:///"):
        path = url.removeprefix("sqlite:///")
        if path and path != ":memory:":
            parent = Path(path).parent
            if str(parent) not in ("", "."):
                parent.mkdir(parents=True, exist_ok=True)
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    normalized = _database_url(url)
    options = {}
    if normalized.drivername.startswith("postgresql"):
        options = {
            "pool_pre_ping": True,
            "pool_size": pool_size,
            "max_overflow": max_overflow,
            "pool_recycle": 300,
        }
    engine = create_engine(normalized, connect_args=connect_args, **options)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_conn, _connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def make_session_factory(engine: Engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
