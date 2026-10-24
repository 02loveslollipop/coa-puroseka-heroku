"""Disposable local database fixtures; remote databases are never test targets."""

from datetime import timedelta
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
import psycopg
from psycopg import sql
import pytest
from services.chat import control
from shared.config import settings


@pytest.fixture
def budget_db(monkeypatch):
    url = os.getenv("LOCAL_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires disposable local PostgreSQL")
    if urlsplit(url).hostname not in {"127.0.0.1", "localhost"}:
        pytest.fail("Local database required")
    name = "coa_budget_test_" + uuid4().hex[:10]
    with psycopg.connect(url) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
        ddl = (
            Path(__file__).resolve().parents[1] / "infra/heroku/control.sql"
        ).read_text()
        conn.execute(ddl.replace("{schema}", sql.Identifier(name).as_string(conn)))
        conn.execute(
            sql.SQL(
                "INSERT INTO {}.chat_policies VALUES('demo',true,true,25000,21600)"
            ).format(sql.Identifier(name))
        )
        conn.execute(
            sql.SQL(
                "INSERT INTO {}.chat_budgets(username,resets_at) VALUES('demo',%s)"
            ).format(sql.Identifier(name)),
            (control.utcnow() + timedelta(hours=6),),
        )
    monkeypatch.setitem(settings(), "DATA_SCHEMA", name)
    monkeypatch.setitem(settings(), "CONTROL_DATABASE_URL", url)
    try:
        yield url, name
    finally:
        with psycopg.connect(url) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))
