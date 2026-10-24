"""Immutable semantic release and the only model SQL execution boundary."""

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field
from psycopg import sql
from psycopg import Error as DatabaseError
from shared.config import artifact
from shared.database import connection, schema_name
from services.ontology.catalog import physical_schema, preview
from services.ontology.query import execute_query, QueryRejected
from shared.security import require_service, headers, database_unavailable
from shared.rpc import call
from shared.query_errors import query_diagnostic

app = FastAPI(
    title="COA ontology service", docs_url=None, redoc_url=None, openapi_url=None
)
app.middleware("http")(headers)
app.add_exception_handler(DatabaseError, database_unavailable)


def user(request: Request):
    require_service(request, "ONTOLOGY_SERVICE_TOKEN")
    token = request.headers.get("x-coa-session", "")
    if not token:
        raise HTTPException(401, "Sign in to continue.")
    return call("login", "POST", "/internal/introspect", body={"token": token})


def admin(identity=Depends(user)):
    if identity["role"] != "admin":
        raise HTTPException(403, "Administrator access required.")
    return identity


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "service": "ontology",
        "version": artifact("catalog.json")["version"],
    }


@app.get("/internal/context", dependencies=[Depends(user)])
def context():
    return {
        "catalog": artifact("catalog.json"),
        "physical_schema": physical_schema(),
        "translations": artifact("translations.json"),
        "few_shots": artifact("few_shots.json"),
    }


class Query(BaseModel):
    sql: str = Field(min_length=1, max_length=16000)


@app.post("/internal/query", dependencies=[Depends(user)])
def query(body: Query):
    try:
        return execute_query(body.sql, set(physical_schema()))
    except QueryRejected as exc:
        raise HTTPException(
            400, {"code": "query_rejected", "message": str(exc)}
        ) from None
    except DatabaseError as exc:
        if exc.sqlstate == "57014":
            raise HTTPException(
                408,
                "The query exceeded the 15-second database limit. Narrow the event or rank range and try again.",
            ) from None
        diagnostic = query_diagnostic(exc)
        if diagnostic:
            raise HTTPException(
                422, {"code": "invalid_query", "message": diagnostic}
            ) from None
        raise


@app.get("/api/schema", dependencies=[Depends(admin)])
def schema():
    return physical_schema()


@app.get("/api/tables/{table}", dependencies=[Depends(admin)])
def table(table: str, offset: int = 0):
    if offset < 0:
        raise HTTPException(400, "Offset must be non-negative")
    try:
        return preview(table, offset)
    except ValueError:
        raise HTTPException(404, "Unknown table") from None


@app.get("/api/rankings", dependencies=[Depends(admin)])
def rankings(event_id: int, rank: int):
    if event_id < 1 or rank < 1:
        raise HTTPException(400, "Event and rank must be positive")
    with connection() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '15s'")
        rows = conn.execute(
            sql.SQL(
                "SELECT observed_at,score FROM {}.ranking_observations WHERE event_id=%s AND rank=%s ORDER BY observed_at,row_key LIMIT 10001"
            ).format(sql.Identifier(schema_name())),
            (event_id, rank),
        ).fetchall()
    return {
        "rows": rows[:10000],
        "truncated": len(rows) > 10000,
        "event_id": event_id,
        "rank": rank,
    }


@app.get("/api/ontology.ttl", dependencies=[Depends(admin)])
def ttl():
    return Response(
        artifact("ontology.ttl"),
        media_type="text/turtle",
        headers={"Content-Disposition": 'attachment; filename="puroseka-ontology.ttl"'},
    )


@app.get("/api/{name}", dependencies=[Depends(admin)])
def metadata(name: str):
    allowed = {
        "catalog": "catalog.json",
        "manifest": "manifest.json",
        "ranking-manifest": "ranking_manifest.json",
        "translations": "translations.json",
    }
    if name not in allowed:
        raise HTTPException(404)
    return artifact(allowed[name])
