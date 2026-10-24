"""SQL AST allowlist plus PostgreSQL read-only transaction and resource limits."""

import json
from fastapi.encoders import jsonable_encoder
from sqlglot import parse, exp
from sqlglot.optimizer.scope import Scope, traverse_scope
from shared.database import connection, schema_name, namespace
from shared.query_errors import QueryRejected

ALLOWED_FUNCTIONS = {
    "count",
    "sum",
    "avg",
    "min",
    "max",
    "coalesce",
    "nullif",
    "lower",
    "upper",
    "length",
    "char_length",
    "concat",
    "concat_ws",
    "replace",
    "trim",
    "substring",
    "to_timestamp",
    "to_char",
    "date_part",
    "date_trunc",
    "extract",
    "round",
    "abs",
    "cast",
    "try_cast",
    "greatest",
    "least",
    "distinct",
    "jsonb_extract_path_text",
    "jsonb_array_length",
    "jsonb_typeof",
    "json_extract",
    "json_extract_scalar",
    "jsonb_extract",
    "jsonb_extract_scalar",
    "jsonb_contains",
    "array_agg",
    "string_agg",
    "array_size",
    "current_date",
    "current_timestamp",
    "unix_to_time",
    "time_to_str",
    "timestamp_trunc",
    "at_time_zone",
    "lag",
    "lead",
    "row_number",
    "rank",
    "dense_rank",
    "first_value",
    "last_value",
    "floor",
    "ceil",
    "ceiling",
    "and",
    "or",
    "not",
}


def validate_sql(statement, tables):
    if len(statement) > 16000:
        raise QueryRejected("Query is too long")
    try:
        trees = parse(statement, read="postgres")
    except Exception as e:
        raise QueryRejected("SQL could not be parsed") from e
    if len(trees) != 1 or not isinstance(trees[0], exp.Select):
        raise QueryRejected("Only one SELECT statement is permitted")
    tree = trees[0]
    if any(
        isinstance(
            n,
            (
                exp.Insert,
                exp.Update,
                exp.Delete,
                exp.Create,
                exp.Drop,
                exp.Command,
                exp.Into,
                exp.Lock,
                exp.Copy,
            ),
        )
        for n in tree.walk()
    ):
        raise QueryRejected("Only read-only SELECT queries are permitted")
    for scope in traverse_scope(tree):
        for table in scope.tables:
            # Resolve CTEs in their actual scope. A CTE in an unrelated nested
            # query must never authorize a physical private table of that name.
            source = scope.sources.get(table.alias_or_name)
            if isinstance(source, Scope) and not table.db and not table.catalog:
                continue
            if (
                table.catalog
                or table.db not in ("", schema_name())
                or table.name not in tables
            ):
                raise QueryRejected("Only the imported data tables may be queried")
            table.set("db", exp.Identifier(this=schema_name()))
    for f in tree.find_all(exp.Func):
        name = f.name.lower() if isinstance(f, exp.Anonymous) else f.sql_name().lower()
        if name not in ALLOWED_FUNCTIONS:
            raise QueryRejected(f"Function {name} is not permitted")
    return tree.sql(dialect="postgres")


def execute_query(statement, tables):
    validated = validate_sql(statement, tables)
    with connection() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '15s'")
        conn.execute("SET LOCAL lock_timeout = '1s'")
        conn.execute(f"SET LOCAL search_path = {namespace()}, pg_catalog")
        # Server-side outer limit caps output even if the model omits LIMIT.
        cursor = conn.execute("SELECT * FROM (" + validated + ") AS answer LIMIT 101")
        rows = cursor.fetchall()
        # Keep both browser traces and model evidence bounded for large JSON source rows.
    bounded = []
    size = 0
    truncated = len(rows) > 100
    for row in rows[:100]:
        row = jsonable_encoder(row)
        for key, value in list(row.items()):
            encoded = json.dumps(value, ensure_ascii=False)
            if len(encoded) > 2000:
                row[key] = encoded[:2000] + "… [value truncated]"
                truncated = True
        length = len(json.dumps(row, ensure_ascii=False).encode())
        if size + length > 24000:
            truncated = True
            break
        size += length
        bounded.append(row)
    return {
        "sql": validated,
        "rows": bounded,
        "truncated": truncated,
        "row_limit": 100,
        "evidence_byte_limit": 24000,
    }
