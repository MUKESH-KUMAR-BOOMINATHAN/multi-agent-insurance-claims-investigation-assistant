"""
engine.py

Database connections for both backends.

  * DATABASE_URL unset  -> SQLite files under DATA_DIR (local development, tests).
  * DATABASE_URL set    -> that Postgres database (production). Neon, Render,
                           Supabase and Heroku style URLs all work, including
                           the old ``postgres://`` scheme.

The schema is created and migrated forward automatically (``ensure_schema``):
missing tables are created and missing columns are added, so an existing
database keeps its rows when the app gains a column.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Connection, Engine, MetaData, create_engine, event, inspect, text
from sqlalchemy.pool import NullPool

import config

log = logging.getLogger(__name__)

_engines: dict[str, Engine] = {}
_ready: set[tuple[str, int]] = set()
_lock = threading.Lock()


def url_for(sqlite_path: Path) -> str:
    """The configured Postgres URL, or a SQLite file URL for local use."""
    if config.DATABASE_URL:
        return config.DATABASE_URL
    return f"sqlite:///{Path(sqlite_path).as_posix()}"


def _normalise(url: str) -> str:
    # SQLAlchemy needs the driver named; hosts hand out postgres:// or postgresql://.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def is_postgres(url: str) -> bool:
    return _normalise(url).startswith("postgresql")


def get_engine(url: str) -> Engine:
    with _lock:
        engine = _engines.get(url)
        if engine is not None:
            return engine
        real = _normalise(url)
        if real.startswith("sqlite"):
            Path(real.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
            # No pool: each block opens and closes its own connection, so files
            # are never held open (matters on Windows and in tests).
            engine = create_engine(real, poolclass=NullPool, connect_args={"timeout": 10})

            @event.listens_for(engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
                dbapi_conn.execute("PRAGMA journal_mode=WAL")  # readers never block the writer
        else:
            # Small pool: one API worker. pre_ping + recycle survive a free
            # database that suspends when idle (Neon) or drops idle connections.
            engine = create_engine(
                real,
                pool_size=3,
                max_overflow=2,
                pool_pre_ping=True,
                pool_recycle=300,
                connect_args={"connect_timeout": 15},
            )
        _engines[url] = engine
        return engine


def ensure_schema(url: str, metadata: MetaData) -> Engine:
    """Create missing tables/indexes and add missing columns (once per process)."""
    engine = get_engine(url)
    key = (url, id(metadata))
    if key in _ready:
        return engine
    with _lock:
        if key in _ready:
            return engine
        with engine.begin() as conn:
            _add_missing_columns(conn, metadata)
            metadata.create_all(conn)
        _ready.add(key)
    return engine


def _add_missing_columns(conn: Connection, metadata: MetaData) -> None:
    """Additive migration for tables created by an older version of the app."""
    inspector = inspect(conn)
    existing_tables = set(inspector.get_table_names())
    for table in metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        present = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present:
                continue
            col_type = column.type.compile(dialect=conn.dialect)
            default = ""
            if column.server_default is not None:
                arg = column.server_default.arg
                literal = f"'{arg}'" if isinstance(arg, str) else str(arg.compile(dialect=conn.dialect))
                default = f" DEFAULT {literal}"
            # Added columns are nullable: existing rows have no value for them.
            conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{column.name}" {col_type}{default}'))  # nosec B608 - names come from our own schema
            log.info("Migrated: added column %s.%s", table.name, column.name)
            if table.name == "users" and column.name == "username_key":
                conn.execute(text("UPDATE users SET username_key = lower(username)"))
        # create_all() only creates indexes for NEW tables; add any the table is missing.
        present_indexes = {i["name"] for i in inspector.get_indexes(table.name)}
        for index in table.indexes:
            if index.name not in present_indexes:
                index.create(conn)
                log.info("Migrated: added index %s", index.name)


@contextmanager
def transaction(url: str, metadata: MetaData) -> Iterator[Connection]:
    """A connection inside a transaction: commit on success, roll back on error, always closed."""
    engine = ensure_schema(url, metadata)
    with engine.begin() as conn:
        yield conn


def reset_for_tests() -> None:
    """Forget engines and migrated schemas (tests point modules at fresh databases)."""
    with _lock:
        for engine in _engines.values():
            engine.dispose()
        _engines.clear()
        _ready.clear()
