"""Execute firewall-approved SQL inside a bounded read-only transaction."""

import json
from fastapi.encoders import jsonable_encoder
from shared.database import connection
from shared.query_errors import QueryRejected as QueryRejected
from services.ontology.firewall import validate_sql


def execute_query(statement, tables):
    validated = validate_sql(statement, tables)
    with connection() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '15s'")
        conn.execute("SET LOCAL lock_timeout = '1s'")
        conn.execute("SET LOCAL search_path = pg_catalog")
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
