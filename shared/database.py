from contextlib import contextmanager
import re
from threading import BoundedSemaphore
from psycopg import connect
from psycopg.rows import dict_row
from shared.config import settings

_slots = BoundedSemaphore(2)


def namespace():
    name = settings().get("DATA_SCHEMA", "coa_puroseka")
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name):
        raise ValueError("Invalid application schema")
    return '"' + name + '"'


def schema_name():
    return namespace()[1:-1]


@contextmanager
def connection():
    if not _slots.acquire(timeout=5):
        raise RuntimeError("Database connection capacity reached")
    try:
        with connect(
            settings()["DATABASE_URL"],
            connect_timeout=8,
            row_factory=dict_row,
            # Source/ontology reads never share the writable control connection.
            # Approved tables are qualified explicitly; only built-ins resolve
            # through the search path, preventing application-function shadowing.
            options="-c default_transaction_read_only=on -c search_path=pg_catalog",
        ) as conn:
            yield conn
    finally:
        _slots.release()
