"""Regression coverage for the missing ranking ID and escaped alias failures."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from services.chat import inference, tracing
from shared.query_errors import QueryError, QueryRejected, query_diagnostic
from shared import rpc


@pytest.mark.parametrize(
    "failure",
    [
        "column r1.id does not exist",
        'missing FROM-clause entry for table "r"',
    ],
)
def test_one_correction_uses_diagnostic_and_charges_both_calls(monkeypatch, failure):
    model_calls, sql_calls = [], []

    async def model(messages, json_mode=False):
        model_calls.append(messages)
        return json.dumps(
            {
                "sql": "SELECT score FROM ranking_observations",
                "reason": "Observed scores",
            }
        ), {"total_tokens": 100}

    def remote(service, method, path, **kwargs):
        if path == "/internal/context":
            return {
                "catalog": {"version": "test"},
                "physical_schema": {"ranking_observations": {"score": "bigint"}},
            }
        sql_calls.append(kwargs["body"]["sql"])
        if len(sql_calls) == 1:
            raise QueryError(failure)
        return {"sql": sql_calls[-1], "rows": [{"score": 42}], "truncated": False}

    monkeypatch.setattr(inference, "complete", model)
    monkeypatch.setattr(inference, "call", remote)
    trace = []
    token = tracing.calls.set(trace)
    try:
        result = asyncio.run(inference.infer("Compare observed scores", "base", []))
    finally:
        tracing.calls.reset(token)
    assert result["status"] == "ok" and result["query_corrected"]
    assert len(sql_calls) == len(model_calls) == 2
    assert failure in model_calls[1][-1]["content"]
    assert result["usage"]["total_tokens"] == 200
    assert [x["status"] for x in trace if x["name"] == "execute_query"] == [
        "error",
        "ok",
    ]


@pytest.mark.parametrize(
    "error,expected_calls",
    [(QueryError("Bad alias"), 2), (QueryRejected("Private table"), 1)],
)
def test_retry_is_bounded_and_safety_rejections_never_retry(
    monkeypatch, error, expected_calls
):
    calls = []

    async def model(*args):
        calls.append(1)
        return '{"sql":"SELECT score FROM ranking_observations"}', {"total_tokens": 1}

    def remote(service, method, path, **kwargs):
        if path.endswith("context"):
            return {
                "catalog": {"version": "test"},
                "physical_schema": {"ranking_observations": {"score": "bigint"}},
            }
        raise error

    monkeypatch.setattr(inference, "complete", model)
    monkeypatch.setattr(inference, "call", remote)
    result = asyncio.run(inference.infer("Question", "base", []))
    assert result["status"] == "error" and len(calls) == expected_calls


def test_sql_diagnostics_never_echo_values_or_connection_details():
    error = SimpleNamespace(
        sqlstate="42703",
        diag=SimpleNamespace(
            message_primary="secret-password private-host user question"
        ),
    )
    assert query_diagnostic(error).startswith("Undefined column;")
    error.sqlstate = "08006"
    assert query_diagnostic(error) is None


def test_rpc_preserves_typed_query_error_only_at_query_boundary(monkeypatch):
    monkeypatch.setitem(rpc.settings(), "ONTOLOGY_SERVICE_URL", "https://ontology.test")
    monkeypatch.setitem(rpc.settings(), "ONTOLOGY_SERVICE_TOKEN", "local-test")
    original_client = httpx.Client
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            422,
            json={"detail": {"code": "invalid_query", "message": "Undefined column"}},
        )
    )
    monkeypatch.setattr(
        rpc.httpx,
        "Client",
        lambda **kwargs: original_client(transport=transport, **kwargs),
    )
    with pytest.raises(QueryError, match="Undefined column"):
        rpc.call(
            "ontology",
            "POST",
            "/internal/query",
            body={"sql": "SELECT missing FROM events"},
        )


@pytest.mark.parametrize(
    "statement,diagnostic",
    [
        ("SELECT r1.id FROM ranking_observations r1", "column r1.id does not exist"),
        (
            "WITH daily AS (SELECT score FROM ranking_observations r) SELECT r.score FROM daily",
            'missing FROM-clause entry for table "r"',
        ),
    ],
)
def test_real_postgres_error_crosses_ontology_boundary_safely(
    budget_db, monkeypatch, statement, diagnostic
):
    import psycopg
    from psycopg import sql
    from fastapi.testclient import TestClient
    from services.ontology import main as ontology
    from shared.config import settings

    url, name = budget_db
    with psycopg.connect(url) as conn:
        conn.execute(
            sql.SQL(
                "CREATE TABLE {}.ranking_observations(row_key text, score bigint)"
            ).format(sql.Identifier(name))
        )
    monkeypatch.setitem(settings(), "DATABASE_URL", url)
    monkeypatch.setattr(
        ontology,
        "physical_schema",
        lambda: {"ranking_observations": {"row_key": "text", "score": "bigint"}},
    )
    monkeypatch.setitem(
        ontology.app.dependency_overrides, ontology.user, lambda: {"role": "user"}
    )
    response = TestClient(ontology.app).post("/internal/query", json={"sql": statement})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_query"
    assert diagnostic in response.json()["detail"]["message"]
