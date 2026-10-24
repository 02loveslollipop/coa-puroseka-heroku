"""Adversarial SQL must stop before execution; PostgreSQL is a second barrier."""

import json
from pathlib import Path

import psycopg
from psycopg import sql
import pytest
from fastapi.testclient import TestClient

from services.ontology import query
from services.ontology.firewall import validate_sql
from shared.database import connection, schema_name
from shared.config import settings
from shared.query_errors import QueryRejected


ATTACKS = [
    "DELETE FROM events",
    "UPDATE events SET name='poisoned'",
    "INSERT INTO events(id) VALUES(999)",
    "MERGE INTO events e USING cards c ON e.id=c.id WHEN MATCHED THEN DELETE",
    "TRUNCATE events",
    "ALTER TABLE events ADD COLUMN poisoned text",
    "DROP TABLE events",
    "CREATE TABLE poisoned(id int)",
    "GRANT ALL ON events TO public",
    "REVOKE SELECT ON events FROM public",
    "COPY events FROM STDIN",
    "COPY events TO PROGRAM 'true'",
    "DO $$ BEGIN DELETE FROM events; END $$",
    "CALL poison()",
    "SET default_transaction_read_only=off",
    "RESET ALL",
    "BEGIN READ WRITE",
    "COMMIT",
    "PREPARE poison AS DELETE FROM events",
    "EXECUTE poison",
    "EXPLAIN ANALYZE DELETE FROM events",
    "SELECT 1; DELETE FROM events",
    "SELECT 1; /* hidden write */ UPDATE events SET name='poisoned'",
    "SELECT * INTO poisoned FROM events",
    "SELECT * FROM events FOR UPDATE",
    "SELECT * FROM events FOR SHARE",
    "WITH x AS (DELETE FROM events RETURNING *) SELECT * FROM x",
    "WITH x AS (UPDATE events SET name='poisoned' RETURNING *) SELECT * FROM x",
    "WITH x AS (INSERT INTO events(id) VALUES(999) RETURNING *) SELECT * FROM x",
    "SELECT set_config('transaction_read_only','off',true)",
    "SELECT nextval('events_id_seq')",
    "SELECT setval('events_id_seq',1)",
    "SELECT lo_import('/etc/passwd')",
    "SELECT lo_export(1,'/tmp/poisoned')",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT pg_sleep(10)",
    "SELECT pg_advisory_lock(1)",
    "SELECT pg_terminate_backend(1)",
    "SELECT dblink_exec('remote','DELETE FROM events')",
    "SELECT query_to_xml('SELECT * FROM auth_accounts',false,false,'')",
    "SELECT public.abs(1)",
    "SELECT coa_puroseka.count(1)",
    "SELECT pg_temp.lower('x')",
    "SELECT pg_catalog.abs(1)",
    "SELECT 1 OPERATOR(public.+) 2",
    "SELECT 'x'::public.custom_type",
    "SELECT 'events'::regclass",
    "SELECT 'x'::regprocedure",
    "SELECT '<x/>'::xml",
    "SELECT * FROM pg_catalog.pg_roles",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM auth_accounts",
    "SELECT * FROM coa_puroseka.chat_jobs",
    "SELECT e.id FROM events e JOIN auth_sessions s ON true",
    "SELECT * FROM events WHERE id IN (SELECT 1 FROM auth_accounts)",
    "SELECT * FROM (SELECT id FROM events UNION ALL SELECT 1 FROM auth_accounts) x",
    "WITH x AS (WITH auth_accounts AS (SELECT 1) SELECT * FROM auth_accounts) SELECT * FROM auth_accounts",
    "SELECT * FROM other_schema.events",
    "SELECT * FROM other_database.coa_puroseka.events",
    "SELECT * FROM abs(1)",
    "WITH RECURSIVE x AS (SELECT 1 AS id UNION ALL SELECT id+1 FROM x) SELECT * FROM x",
    "SELECT * FROM events TABLESAMPLE SYSTEM (1)",
    "SELECT * FROM (VALUES (1)) x(id)",
]


@pytest.mark.parametrize("statement", ATTACKS)
def test_firewall_rejects_before_opening_execution_connection(statement, monkeypatch):
    def forbidden():
        pytest.fail("Rejected SQL must never reach the execution connection")

    monkeypatch.setattr(query, "connection", forbidden)
    with pytest.raises(QueryRejected):
        query.execute_query(statement, {"events", "cards"})


def test_reviewed_examples_and_analytics_still_work():
    root = Path(__file__).resolve().parents[1] / "services/ontology/semantic"
    definitions = json.loads((root / "definitions.json").read_text())
    tables = (
        set(definitions["entities"])
        | set(definitions["collections"])
        | {"ranking_observations"}
    )
    for example in json.loads((root / "few_shots.json").read_text()):
        assert validate_sql(
            example["sql"].replace("data.", schema_name() + "."), tables
        )
    for statement in [
        "SELECT CASE WHEN id>0 THEN 'yes' ELSE 'no' END FROM events",
        "SELECT string_agg(name,',' ORDER BY id),array_agg(id) FROM events",
        "SELECT raw_record->>'id',raw_record->'cards'->0 FROM events",
        "SELECT sum(id) FILTER (WHERE id>0) OVER (ORDER BY id ROWS BETWEEN 1 PRECEDING AND CURRENT ROW) FROM events",
        "SELECT count(*) FROM events WHERE EXISTS (SELECT 1 FROM cards WHERE cards.id=events.id)",
        "SELECT 'DROP TABLE events; --' AS text FROM events WHERE name='DELETE'",
    ]:
        assert validate_sql(statement, tables)


def test_comments_removed_and_complexity_bounded():
    assert "/*" not in validate_sql(
        "SELECT /* DELETE FROM events */ count(*) FROM events", {"events"}
    )
    with pytest.raises(QueryRejected, match="complex"):
        validate_sql("SELECT " + ",".join("1" for _ in range(1001)), {"events"})
    with pytest.raises(QueryRejected, match="complex"):
        validate_sql("SELECT " + "(" * 70 + "1" + ")" * 70, {"events"})


def test_ontology_endpoint_reports_firewall_rejection(monkeypatch):
    from services.ontology import main

    monkeypatch.setitem(settings(), "ONTOLOGY_SERVICE_TOKEN", "test-service-token")
    monkeypatch.setattr(
        main, "call", lambda *a, **k: {"role": "user", "username": "demo"}
    )
    monkeypatch.setattr(main, "physical_schema", lambda: {"events": {}})

    def forbidden():
        pytest.fail("Rejected SQL must not reach PostgreSQL")

    monkeypatch.setattr(query, "connection", forbidden)
    client = TestClient(main.app)
    for statement in (
        "WITH x AS (DELETE FROM events RETURNING *) SELECT * FROM x",
        "SELECT public.abs(1)",
    ):
        response = client.post(
            "/internal/query",
            json={"sql": statement},
            headers={
                "Authorization": "Bearer test-service-token",
                "X-COA-Session": "test-session",
            },
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "query_rejected"


def test_database_readonly_and_builtin_resolution(budget_db, monkeypatch):
    url, name = budget_db
    monkeypatch.setitem(settings(), "DATABASE_URL", url)
    schema = sql.Identifier(name)
    # Synthetic local-only tripwire. This account can write, deliberately testing
    # the transaction barrier even if credentials are misconfigured later.
    with psycopg.connect(url) as writer:
        writer.execute(
            sql.SQL("CREATE TABLE {}.events(id int,name text)").format(schema)
        )
        writer.execute(
            sql.SQL("INSERT INTO {}.events VALUES(1,'original')").format(schema)
        )
        writer.execute(sql.SQL("CREATE TABLE {}.tripwire(id int)").format(schema))
        writer.execute(
            sql.SQL(
                "CREATE FUNCTION {}.abs(integer) RETURNS integer LANGUAGE plpgsql AS $$ "
                "BEGIN INSERT INTO {}.tripwire VALUES(1); RETURN 99; END $$"
            ).format(schema, schema)
        )
    with connection() as conn:
        assert (
            conn.execute("SHOW default_transaction_read_only").fetchone()[
                "default_transaction_read_only"
            ]
            == "on"
        )
        assert (
            conn.execute("SHOW search_path").fetchone()["search_path"] == "pg_catalog"
        )
    for statement in [
        sql.SQL("DELETE FROM {}.events").format(schema),
        sql.SQL("UPDATE {}.events SET name='poisoned'").format(schema),
        sql.SQL("CREATE TABLE {}.poisoned(id int)").format(schema),
    ]:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            with connection() as conn:
                conn.execute(statement)
    assert query.execute_query("SELECT abs(-3) AS value", {"events"})["rows"] == [
        {"value": 3}
    ]
    assert query.execute_query("SELECT id,name FROM events", {"events"})["rows"] == [
        {"id": 1, "name": "original"}
    ]
    with psycopg.connect(url) as writer:
        assert (
            writer.execute(
                sql.SQL("SELECT count(*) FROM {}.tripwire").format(schema)
            ).fetchone()[0]
            == 0
        )
