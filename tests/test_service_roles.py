"""Exercise service workflows and hostile SQL using real restricted PG roles."""

from contextlib import contextmanager
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from scripts.provision_service_roles import (
    POLICIES,
    SOURCE_TABLES,
    apply_grants,
    check_role,
    configure_role,
    database_url,
    service_database,
)
from services.chat import control
from services.login import accounts


@pytest.fixture
def service_roles(budget_db, monkeypatch):
    url, schema = budget_db
    roles = {}
    with psycopg.connect(url) as conn:
        for table in SOURCE_TABLES:
            conn.execute(
                sql.SQL("CREATE TABLE {}.{} (id integer PRIMARY KEY)").format(
                    sql.Identifier(schema), sql.Identifier(table)
                )
            )
            conn.execute(
                sql.SQL("INSERT INTO {}.{} VALUES(1)").format(
                    sql.Identifier(schema), sql.Identifier(table)
                )
            )
        conn.execute(
            sql.SQL("INSERT INTO {}.auth_accounts VALUES(%s,'user',%s,true)").format(
                sql.Identifier(schema)
            ),
            ("demo", accounts.password_hash("local-password")),
        )
    try:
        with psycopg.connect(url) as conn:
            for service in POLICIES:
                roles[service] = configure_role(conn, schema, service, uuid4().hex)

        @contextmanager
        def restricted(service):
            with psycopg.connect(url, row_factory=dict_row) as conn:
                conn.execute(
                    sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(roles[service]))
                )
                # SET ROLE does not load login defaults; still prove ACLs when
                # transaction_read_only is off and application firewall is absent.
                yield conn

        monkeypatch.setattr(accounts, "control_connection", lambda: restricted("login"))
        monkeypatch.setattr(control, "control_connection", lambda: restricted("chat"))
        yield schema, roles, restricted
    finally:
        with psycopg.connect(url) as conn:
            for role in roles.values():
                conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_login_and_chat_work_under_separate_column_grants(service_roles):
    token, user = accounts.login("demo", "local-password", "127.0.0.1")
    assert user["role"] == "user" and accounts.identity(token)["username"] == "demo"
    before = control.usage("demo")
    reservation = control.reserve("demo", 100)
    control.settle("demo", reservation, 40)
    assert control.usage("demo")["used_tokens"] == before["used_tokens"] + 40
    control.update_limit("demo", True, 100000, 21600)
    assert control.usage("demo")["token_limit"] == 100000
    job = control.create_job("demo", {"question": "local test"})
    control.update_job_activity(job, [{"status": "running"}])
    control.finish_job(job, {"answer": "completed"})
    assert control.get_job("demo", job)["state"] == "done"
    control.interrupt_jobs()
    control.clean_demo_jobs()
    accounts.revoke(token)
    with pytest.raises(Exception) as error:
        accounts.identity(token)
    assert error.value.status_code == 401


@pytest.mark.parametrize(
    "service,statement",
    [
        ("ontology", "INSERT INTO {s}.events VALUES(2)"),
        ("ontology", "UPDATE {s}.events SET id=2"),
        ("ontology", "DELETE FROM {s}.events"),
        ("ontology", "TRUNCATE {s}.events"),
        ("ontology", "CREATE TABLE {s}.poisoned(id integer)"),
        ("ontology", "ALTER TABLE {s}.events ADD COLUMN poisoned text"),
        ("ontology", "DROP TABLE {s}.events"),
        ("ontology", "SELECT * FROM {s}.auth_accounts"),
        ("ontology", "SELECT * FROM {s}.auth_sessions"),
        ("ontology", "SELECT * FROM {s}.chat_jobs"),
        ("ontology", "UPDATE {s}.chat_policies SET limit_enabled=false"),
        ("ontology", "SELECT * FROM {s}.events FOR UPDATE"),
        ("login", "UPDATE {s}.auth_accounts SET role='admin'"),
        ("login", "UPDATE {s}.auth_accounts SET enabled=false"),
        ("login", "SELECT * FROM {s}.events"),
        ("login", "UPDATE {s}.chat_policies SET token_limit=999999"),
        ("chat", "SELECT * FROM {s}.auth_accounts"),
        ("chat", "SELECT * FROM {s}.auth_sessions"),
        ("chat", "SELECT * FROM {s}.events"),
        ("chat", "UPDATE {s}.chat_policies SET enabled=false"),
        ("chat", "UPDATE {s}.chat_jobs SET username='other'"),
        ("chat", "UPDATE {s}.chat_reservations SET amount=0"),
    ],
)
def test_acl_blocks_hostile_statements_without_firewall(
    service_roles, service, statement
):
    schema, _, restricted = service_roles
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with restricted(service) as conn:
            conn.execute(sql.SQL(statement).format(s=sql.Identifier(schema)))


def test_ontology_reads_registered_tables_and_metadata(service_roles):
    schema, _, restricted = service_roles
    with restricted("ontology") as conn:
        for table in (*SOURCE_TABLES, "ontology_versions"):
            conn.execute(
                sql.SQL("SELECT * FROM {}.{} LIMIT 1").format(
                    sql.Identifier(schema), sql.Identifier(table)
                )
            ).fetchall()


def test_reapplying_policy_removes_old_column_grants(service_roles, budget_db):
    schema, roles, restricted = service_roles
    role = roles["chat"]
    with psycopg.connect(budget_db[0]) as conn:
        conn.execute(
            sql.SQL("GRANT UPDATE(enabled) ON {}.chat_policies TO {}").format(
                sql.Identifier(schema), sql.Identifier(role)
            )
        )
        apply_grants(conn, schema, role, "chat")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with restricted("chat") as conn:
            conn.execute(
                sql.SQL("UPDATE {}.chat_policies SET enabled=false").format(
                    sql.Identifier(schema)
                )
            )


def test_roles_have_no_admin_attributes_memberships_or_ownership(
    service_roles, budget_db
):
    schema, roles, restricted = service_roles
    for service, role in roles.items():
        with restricted(service) as conn:
            row = conn.execute(
                "SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls,rolreplication,rolinherit "
                "FROM pg_roles WHERE rolname=current_user"
            ).fetchone()
            assert not any(row.values())
        with psycopg.connect(budget_db[0]) as conn:
            assert check_role(conn, schema, role)


def test_refuses_existing_unmanaged_role(budget_db):
    with psycopg.connect(budget_db[0]) as conn:
        role = conn.execute("SELECT current_user").fetchone()[0]
        with pytest.raises(ValueError, match="not managed"):
            check_role(conn, budget_db[1], role)


def test_url_escapes_credentials_and_deployment_requires_dedicated_tls_roles():
    from psycopg.conninfo import conninfo_to_dict

    base = {
        "host": "db.example.test",
        "port": "5432",
        "dbname": "shared",
        "sslmode": "require",
    }
    password = "private:@/ %value"
    value = database_url(base, "coa_puroseka_ontology", password)
    assert conninfo_to_dict(value)["password"] == password
    assert service_database({"ONTOLOGY_DATABASE_URL": value}, "ontology") == value
    with pytest.raises(ValueError):
        service_database({"MIGRATION_DATABASE_URL": value}, "ontology")
    with pytest.raises(ValueError):
        service_database({"CHAT_DATABASE_URL": value}, "chat")
    with pytest.raises(ValueError):
        service_database(
            {
                "ONTOLOGY_DATABASE_URL": value.replace(
                    "sslmode=require", "sslmode=disable"
                )
            },
            "ontology",
        )


def test_reprovisioning_reuses_owned_role_and_applies_defaults(
    service_roles, budget_db
):
    schema, roles, _ = service_roles
    with psycopg.connect(budget_db[0]) as conn:
        for service, role in roles.items():
            assert configure_role(conn, schema, service, uuid4().hex) == role
            settings = conn.execute(
                "SELECT rolconfig FROM pg_roles WHERE rolname=%s", (role,)
            ).fetchone()[0]
            assert "search_path=pg_catalog" in settings
            assert (
                "default_transaction_read_only="
                + ("on" if service == "ontology" else "off")
                in settings
            )
