from contextlib import contextmanager
from datetime import datetime, timezone
from threading import BoundedSemaphore
import psycopg
from psycopg.rows import dict_row
from shared.config import settings

_slots = BoundedSemaphore(2 if settings().get("COA_SERVICE") == "login" else 4)


@contextmanager
def control_connection():
    if not _slots.acquire(timeout=5):
        raise RuntimeError("Database connection capacity reached")
    try:
        with psycopg.connect(
            settings()["CONTROL_DATABASE_URL"], connect_timeout=8, row_factory=dict_row
        ) as conn:
            yield conn
    finally:
        _slots.release()


def utcnow():
    return datetime.now(timezone.utc)
