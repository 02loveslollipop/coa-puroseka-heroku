"""Persistent login-time upgrades, including concurrent password changes."""

from contextlib import contextmanager
from datetime import timedelta
import hashlib
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest
from fastapi import HTTPException
from argon2 import PasswordHasher, Type
from services.login import accounts
from services.chat import control
from scripts.enable_password_rehash import grant

USERNAME = "legacy@example.test"
PASSWORD = "Local-test-password-7"


def legacy_hash():
    salt = "01" * 16
    return (
        salt
        + ":"
        + hashlib.scrypt(PASSWORD.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
    )


def seed(budget_db, encoded, enabled=True):
    with psycopg.connect(budget_db[0]) as conn:
        conn.execute(
            sql.SQL("INSERT INTO {}.auth_accounts VALUES(%s,%s,%s,%s)").format(
                sql.Identifier(budget_db[1])
            ),
            (USERNAME, "user", encoded, enabled),
        )


def stored(budget_db):
    with psycopg.connect(budget_db[0]) as conn:
        return conn.execute(
            sql.SQL(
                "SELECT password_hash FROM {}.auth_accounts WHERE username=%s"
            ).format(sql.Identifier(budget_db[1])),
            (USERNAME,),
        ).fetchone()[0]


@pytest.mark.parametrize("old", ["scrypt", "argon2"])
def test_successful_login_upgrades_and_preserves_existing_sessions_and_budget(
    budget_db, old
):
    encoded = (
        legacy_hash()
        if old == "scrypt"
        else PasswordHasher(
            time_cost=2, memory_cost=19456, parallelism=1, type=Type.ID
        ).hash(PASSWORD)
    )
    seed(budget_db, encoded)
    before = control.usage("demo")
    with psycopg.connect(budget_db[0]) as conn:
        conn.execute(
            sql.SQL("INSERT INTO {}.auth_sessions VALUES(%s,%s,%s,%s)").format(
                sql.Identifier(budget_db[1])
            ),
            (
                accounts.fingerprint("existing-session"),
                USERNAME,
                "csrf",
                control.utcnow() + timedelta(hours=1),
            ),
        )
    token, user = accounts.login(USERNAME, PASSWORD, "127.0.0.1")
    current = stored(budget_db)
    assert current.startswith("$argon2id$") and current != encoded
    assert accounts.password_matches(PASSWORD, current)
    assert not accounts.password_needs_rehash(current)
    assert accounts.identity(token)["username"] == USERNAME
    assert accounts.identity("existing-session")["username"] == USERNAME
    assert user["role"] == "user" and control.usage("demo") == before
    accounts.login(USERNAME, PASSWORD, "127.0.0.1")
    assert stored(budget_db) == current


@pytest.mark.parametrize("password,enabled", [("wrong", True), (PASSWORD, False)])
def test_failed_or_disabled_login_does_not_upgrade_hash(budget_db, password, enabled):
    encoded = legacy_hash()
    seed(budget_db, encoded, enabled)
    with pytest.raises(HTTPException) as error:
        accounts.login(USERNAME, password, "127.0.0.1")
    assert error.value.status_code == 401 and stored(budget_db) == encoded
    with psycopg.connect(budget_db[0]) as conn:
        assert (
            conn.execute(
                sql.SQL("SELECT count(*) FROM {}.auth_sessions").format(
                    sql.Identifier(budget_db[1])
                )
            ).fetchone()[0]
            == 0
        )


def test_password_reset_between_verification_and_session_creation_is_rechecked(
    budget_db, monkeypatch
):
    seed(budget_db, legacy_hash())
    new = accounts.password_hash("Different-password-9")
    original = accounts.password_matches
    calls = 0

    def verify(password, encoded):
        nonlocal calls
        result = original(password, encoded)
        calls += 1
        if calls == 1:
            with psycopg.connect(budget_db[0]) as conn:
                conn.execute(
                    sql.SQL(
                        "UPDATE {}.auth_accounts SET password_hash=%s WHERE username=%s"
                    ).format(sql.Identifier(budget_db[1])),
                    (new, USERNAME),
                )
        return result

    monkeypatch.setattr(accounts, "password_matches", verify)
    with pytest.raises(HTTPException) as error:
        accounts.login(USERNAME, PASSWORD, "127.0.0.1")
    assert error.value.status_code == 401 and stored(budget_db) == new
    assert calls == 2


def test_column_only_grant_supports_upgrade_without_role_writes(budget_db, monkeypatch):
    url, schema = budget_db
    seed(budget_db, legacy_hash())
    role = "coa_hash_test_" + uuid4().hex[:10]
    try:
        with psycopg.connect(url) as conn:
            conn.execute(sql.SQL("CREATE ROLE {}").format(sql.Identifier(role)))
            conn.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                    sql.Identifier(schema), sql.Identifier(role)
                )
            )
            conn.execute(
                sql.SQL("GRANT SELECT ON {}.auth_accounts TO {}").format(
                    sql.Identifier(schema), sql.Identifier(role)
                )
            )
            for table in ("auth_sessions", "auth_login_attempts"):
                conn.execute(
                    sql.SQL("GRANT SELECT,INSERT,UPDATE,DELETE ON {}.{} TO {}").format(
                        sql.Identifier(schema),
                        sql.Identifier(table),
                        sql.Identifier(role),
                    )
                )
            grant(conn, schema, role)

        @contextmanager
        def restricted():
            with psycopg.connect(url, row_factory=dict_row) as conn:
                conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
                yield conn

        monkeypatch.setattr(accounts, "control_connection", restricted)
        token, _ = accounts.login(USERNAME, PASSWORD, "127.0.0.1")
        assert stored(budget_db).startswith("$argon2id$")
        assert accounts.identity(token)["role"] == "user"
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with restricted() as conn:
                conn.execute(
                    sql.SQL(
                        "UPDATE {}.auth_accounts SET role='admin' WHERE username=%s"
                    ).format(sql.Identifier(schema)),
                    (USERNAME,),
                )
    finally:
        with psycopg.connect(url) as conn:
            conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
