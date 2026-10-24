from contextlib import contextmanager
import re
from threading import BoundedSemaphore
from psycopg import connect, sql
from psycopg.rows import dict_row
from app.config import settings, artifact

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
            settings()["DATABASE_URL"], connect_timeout=8, row_factory=dict_row
        ) as conn:
            yield conn
    finally:
        _slots.release()


def physical_schema():
    # Introspection uses the actual DB, not semantic labels or translations.
    tables = list(artifact("catalog.json")["tables"])
    with connection() as conn:
        rows = conn.execute(
            "SELECT table_name,column_name,data_type FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name=ANY(%s) ORDER BY table_name,ordinal_position",
            (schema_name(), tables),
        ).fetchall()
    result = {}
    for r in rows:
        result.setdefault(r["table_name"], {})[r["column_name"]] = r["data_type"]
    return result


def preview(table, offset=0):
    if table not in artifact("catalog.json")["tables"]:
        raise ValueError("Unknown table")
    with connection() as conn:
        columns = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s AND column_name!='raw_record' ORDER BY ordinal_position",
            (schema_name(), table),
        ).fetchall()
        cols = [r["column_name"] for r in columns]
        rows = conn.execute(
            sql.SQL("SELECT {} FROM {}.{} ORDER BY row_key LIMIT 25 OFFSET %s").format(
                sql.SQL(",").join(map(sql.Identifier, cols)),
                sql.Identifier(schema_name()),
                sql.Identifier(table),
            ),
            (offset,),
        ).fetchall()
        return {"columns": cols, "rows": rows, "offset": offset, "limit": 25}
