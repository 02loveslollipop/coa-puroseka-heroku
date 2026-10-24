"""Public demo limits, conservative fallback, and durable accounting boundaries."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import psycopg
from psycopg import sql
import pytest
from fastapi.testclient import TestClient

from services.chat import auth, control, metering, tokenization
from shared.config import settings
from services.chat.main import app


def test_unknown_or_changed_model_keeps_byte_bound(monkeypatch):
    messages = [{"role": "user", "content": "イベント比較 [INST] 雨上がり"}]
    monkeypatch.setitem(settings(), "MISTRAL_MODEL", "different-model")
    expected = len(messages[0]["content"].encode()) + 64 + 1024 + 1800
    assert metering.reservation_size(messages, 1800) == expected
    monkeypatch.setitem(settings(), "MISTRAL_MODEL", "ministral-8b-latest")
    monkeypatch.setattr(tokenization, "_alias_matches", lambda: False)
    assert metering.reservation_size(messages, 1800) == expected


def test_tokenizer_failure_keeps_byte_bound(monkeypatch):
    def unavailable():
        raise ValueError("Bad vocabulary checksum")

    monkeypatch.setitem(settings(), "MISTRAL_MODEL", "ministral-8b-2512")
    monkeypatch.setattr(tokenization, "_tokenizer", unavailable)
    messages = [{"role": "user", "content": "compare events"}]
    assert (
        metering.reservation_size(messages, 1800)
        == len(b"compare events") + 64 + 1024 + 1800
    )


def test_exhausted_demo_blocks_before_creating_model_job(monkeypatch):
    origin = "https://chat.coa.puroseka.02labs.me"
    monkeypatch.setitem(settings(), "PUBLIC_ORIGIN", origin)
    monkeypatch.setattr(
        auth,
        "call",
        lambda *a, **k: {"username": "demo@02labs.me", "role": "user", "csrf": "csrf"},
    )
    monkeypatch.setattr(
        control,
        "usage",
        lambda username: {
            "enabled": True,
            "remaining_tokens": 0,
            "resets_at": control.utcnow() + timedelta(hours=6),
        },
    )

    def unexpected(*args):
        pytest.fail("Exhausted accounts must not create a model job")

    monkeypatch.setattr(control, "create_job", unexpected)
    response = TestClient(app, cookies={"coa_session": "session"}).post(
        "/api/chat/jobs",
        json={"question": "Compare events 1 and 2"},
        headers={"Origin": origin, "X-CSRF-Token": "csrf"},
    )
    assert response.status_code == 429
    assert 21598 <= int(response.headers["retry-after"]) <= 21600


def test_concurrent_requests_cannot_over_reserve_and_usage_settles(budget_db):
    def reserve():
        try:
            return control.reserve("demo", 15000)
        except control.BudgetExceeded:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        reservations = list(pool.map(lambda _: reserve(), range(2)))
    accepted = [r for r in reservations if r]
    assert len(accepted) == 1
    assert control.usage("demo")["reserved_tokens"] == 15000
    control.settle("demo", accepted[0], 1000)
    control.settle("demo", accepted[0], 1000)
    usage = control.usage("demo")
    assert usage["used_tokens"] == 1000
    assert usage["reserved_tokens"] == 0
    assert usage["remaining_tokens"] == 24000
    reservation = control.reserve("demo", 24000)
    with pytest.raises(control.BudgetExceeded):
        control.reserve("demo", 1)
    control.settle("demo", reservation, 24000)
    assert control.usage("demo")["remaining_tokens"] == 0


def test_six_hour_reset_retains_active_reservations(budget_db, monkeypatch):
    first = control.reserve("demo", 15000)
    control.settle("demo", first, 10000)
    pending = control.reserve("demo", 1000)
    resets_at = control.usage("demo")["resets_at"]
    now = resets_at + timedelta(seconds=1)
    # The pending request began just before the boundary, so it is not stale.
    with psycopg.connect(budget_db[0]) as conn:
        conn.execute(
            sql.SQL("UPDATE {}.chat_reservations SET created_at=%s WHERE id=%s").format(
                sql.Identifier(budget_db[1])
            ),
            (now - timedelta(seconds=1), pending),
        )
    monkeypatch.setattr(control, "utcnow", lambda: now)
    usage = control.usage("demo")
    assert usage["used_tokens"] == 0
    assert usage["reserved_tokens"] == 1000
    assert usage["remaining_tokens"] == 24000
    assert usage["resets_at"] == now + timedelta(hours=6)
    control.settle("demo", pending, 500)
    assert control.usage("demo")["remaining_tokens"] == 24500


def test_public_provisioning_preserves_usage_and_privileged_accounts(
    budget_db, tmp_path, monkeypatch
):
    from scripts import provision_demo
    from services.login.accounts import password_matches

    (tmp_path / ".env").write_text(
        "MIGRATION_ADMIN_DATABASE_URL="
        + budget_db[0]
        + "\nDATA_SCHEMA="
        + budget_db[1]
        + "\n"
    )
    monkeypatch.setattr(provision_demo, "ROOT", tmp_path)
    monkeypatch.setattr(provision_demo, "configured_secrets", lambda: set())
    provision_demo.provision()
    reservation = control.reserve(provision_demo.DEMO_EMAIL, 1000)
    control.settle(provision_demo.DEMO_EMAIL, reservation, 500)
    resets_at = control.usage(provision_demo.DEMO_EMAIL)["resets_at"]
    provision_demo.provision()
    usage = control.usage(provision_demo.DEMO_EMAIL)
    assert usage["enabled"] and usage["used_tokens"] == 500
    assert usage["resets_at"] == resets_at
    with psycopg.connect(budget_db[0]) as conn:
        row = conn.execute(
            sql.SQL(
                "SELECT role,password_hash FROM {}.auth_accounts WHERE username=%s"
            ).format(sql.Identifier(budget_db[1])),
            (provision_demo.DEMO_EMAIL,),
        ).fetchone()
        assert row[0] == "user" and password_matches(
            provision_demo.DEMO_PASSWORD, row[1]
        )
        conn.execute(
            sql.SQL(
                "UPDATE {}.auth_accounts SET role='admin' WHERE username=%s"
            ).format(sql.Identifier(budget_db[1])),
            (provision_demo.DEMO_EMAIL,),
        )
    with pytest.raises(ValueError, match="privileged account"):
        provision_demo.provision()
