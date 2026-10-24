"""Shared-database protection checks using a tiny synthetic source."""

import json
import os
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import pytest
from scripts.migrate_postgres import migrate

pytestmark = pytest.mark.skipif(
    not os.getenv("LOCAL_TEST_DATABASE_URL"),
    reason="Requires disposable local PostgreSQL",
)


@pytest.fixture
def databases(tmp_path):
    url = os.environ["LOCAL_TEST_DATABASE_URL"]
    if urlsplit(url).hostname not in {"127.0.0.1", "localhost"}:
        pytest.fail("Local database required")
    names = [
        "coa_test_source_" + uuid4().hex[:10],
        "coa_test_target_" + uuid4().hex[:10],
    ]
    with psycopg.connect(url, autocommit=True) as admin:
        for name in names:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    source = psycopg.conninfo.make_conninfo(url, dbname=names[0])
    target = psycopg.conninfo.make_conninfo(url, dbname=names[1])
    try:
        with psycopg.connect(source) as c:
            c.execute(
                "CREATE SCHEMA data; CREATE TABLE data.events(row_key text PRIMARY KEY,id bigint UNIQUE,raw_record jsonb); CREATE SCHEMA control; CREATE TABLE control.accounts(username text PRIMARY KEY,role text,password_hash text,enabled boolean,limit_enabled boolean,token_limit bigint,window_seconds integer); CREATE TABLE control.budgets(username text,used_tokens bigint,reserved_tokens bigint,started_at timestamptz,resets_at timestamptz); CREATE TABLE control.reservations(id uuid,username text,amount bigint,state text,charged_tokens bigint,created_at timestamptz); CREATE TABLE control.jobs(id uuid,username text,state text,request jsonb,result jsonb,created_at timestamptz)"
            )
            c.execute(
                "INSERT INTO data.events VALUES('event/1',1,%s)",
                (Jsonb({"original": "data.events", "name": "雨上がり"}),),
            )
            c.execute(
                "INSERT INTO control.accounts VALUES('demo@example.test','admin',NULL,true,true,100000,3600)"
            )
            c.execute(
                "INSERT INTO control.budgets VALUES('demo@example.test',12345,0,now(),now()+interval '1 hour')"
            )
        with psycopg.connect(target) as c:
            c.execute(
                "CREATE SCHEMA unrelated; CREATE TABLE unrelated.sentinel(value text); INSERT INTO unrelated.sentinel VALUES('preserve this row')"
            )
        catalog = {"version": "test-1", "tables": {"events": {}}, "relationships": []}
        for name, value in {
            "catalog.json": catalog,
            "manifest.json": {},
            "ranking_manifest.json": {},
            "translations.json": {"labels": []},
            "few_shots.json": [
                {"question": "Count events", "sql": "SELECT count(*) FROM data.events"}
                for _ in range(5)
            ],
        }.items():
            (tmp_path / name).write_text(json.dumps(value))
        (tmp_path / "ontology.ttl").write_text(
            "@prefix demo: <https://example.test/> ."
        )
        yield source, target, tmp_path
    finally:
        with psycopg.connect(url, autocommit=True) as admin:
            for name in names:
                admin.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                        sql.Identifier(name)
                    )
                )


def test_migration_preserves_content_policy_and_unrelated_objects(databases):
    source, target, artifacts = databases
    report = migrate(
        source,
        target,
        "coa_test",
        artifacts,
        "demo@example.test",
        "local-test-password-only",
    )
    assert report["unrelated_objects_preserved"] and report["few_shots_verified"] == 5
    with psycopg.connect(target) as c:
        assert c.execute("SELECT raw_record FROM coa_test.events").fetchone()[0] == {
            "original": "data.events",
            "name": "雨上がり",
        }
        assert (
            c.execute("SELECT used_tokens FROM coa_test.chat_budgets").fetchone()[0]
            == 12345
        )
        assert (
            c.execute("SELECT limit_enabled FROM coa_test.chat_policies").fetchone()[0]
            is False
        )
        assert (
            c.execute("SELECT count(*) FROM coa_test.auth_sessions").fetchone()[0] == 0
        )
        assert (
            c.execute("SELECT value FROM unrelated.sentinel").fetchone()[0]
            == "preserve this row"
        )


def test_failed_migration_rolls_back_new_namespace(databases):
    source, target, artifacts = databases
    with pytest.raises(ValueError, match="administrator does not match"):
        migrate(
            source,
            target,
            "coa_test",
            artifacts,
            "wrong@example.test",
            "local-test-password-only",
        )
    with psycopg.connect(target) as c:
        assert (
            c.execute("SELECT 1 FROM pg_namespace WHERE nspname='coa_test'").fetchone()
            is None
        )
        assert (
            c.execute("SELECT value FROM unrelated.sentinel").fetchone()[0]
            == "preserve this row"
        )


def test_existing_namespace_is_never_adopted_or_reset(databases):
    source, target, artifacts = databases
    with psycopg.connect(target) as c:
        c.execute(
            "CREATE SCHEMA coa_test; CREATE TABLE coa_test.sentinel(value integer); INSERT INTO coa_test.sentinel VALUES(7)"
        )
    with pytest.raises(ValueError, match="already exists"):
        migrate(
            source,
            target,
            "coa_test",
            artifacts,
            "demo@example.test",
            "local-test-password-only",
        )
    with psycopg.connect(target) as c:
        assert c.execute("SELECT value FROM coa_test.sentinel").fetchone()[0] == 7


def test_admin_import_grants_runtime_reads_without_source_writes(databases):
    source, target, artifacts = databases
    role = "coa_runtime_test_" + uuid4().hex[:10]
    with psycopg.connect(target) as owner:
        owner.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal("local-runtime-test-only")
            )
        )
    try:
        report = migrate(
            source,
            target,
            "coa_test",
            artifacts,
            "demo@example.test",
            "local-test-password-only",
            runtime_role=role,
        )
        assert report["runtime_source_write_access"] is False
        runtime = psycopg.conninfo.make_conninfo(
            target, user=role, password="local-runtime-test-only"
        )
        with psycopg.connect(runtime) as c:
            assert c.execute("SELECT count(*) FROM coa_test.events").fetchone()[0] == 1
            assert (
                c.execute(
                    "SELECT has_table_privilege('coa_test.events','INSERT')"
                ).fetchone()[0]
                is False
            )
            assert (
                c.execute(
                    "SELECT has_table_privilege('coa_test.auth_accounts','UPDATE')"
                ).fetchone()[0]
                is False
            )
            assert (
                c.execute(
                    "SELECT has_schema_privilege('coa_test','CREATE')"
                ).fetchone()[0]
                is False
            )
            c.execute(
                "INSERT INTO coa_test.auth_login_attempts VALUES('test-key',1,now())"
            )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with psycopg.connect(runtime) as c:
                c.execute("INSERT INTO coa_test.events VALUES('bad',2,'{}')")
    finally:
        with psycopg.connect(target) as owner:
            owner.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            owner.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
