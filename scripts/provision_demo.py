"""Provision the intentionally public chat-only demo without resetting usage."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import psycopg
from psycopg import sql
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.login.accounts import password_hash
from scripts.audit_publication import configured_secrets

ROOT = Path(__file__).resolve().parents[1]
DEMO_EMAIL = "demo@02labs.me"
DEMO_PASSWORD = "demo1234"  # Intentionally public test credentials, never an admin.
TOKEN_LIMIT = 25000
WINDOW_SECONDS = 6 * 60 * 60


def provision():
    cfg = dotenv_values(ROOT / ".env")
    if DEMO_PASSWORD in configured_secrets():
        raise ValueError("The public demo password must not match a private credential")
    owner = cfg.get("MIGRATION_ADMIN_DATABASE_URL")
    if not owner:
        raise ValueError("An operator database connection is required")
    schema = sql.Identifier(cfg.get("DATA_SCHEMA", "coa_puroseka"))
    now = datetime.now(timezone.utc)
    with psycopg.connect(owner, connect_timeout=8) as conn:
        conn.execute("SET LOCAL lock_timeout='5s'")
        row = conn.execute(
            sql.SQL(
                "SELECT role FROM {}.auth_accounts WHERE username=%s FOR UPDATE"
            ).format(schema),
            (DEMO_EMAIL,),
        ).fetchone()
        if row and row[0] != "user":
            raise ValueError("Refusing to alter a privileged account")
        conn.execute(
            sql.SQL(
                "INSERT INTO {}.auth_accounts(username,role,password_hash,enabled) VALUES(%s,'user',%s,true) ON CONFLICT(username) DO UPDATE SET password_hash=EXCLUDED.password_hash,enabled=true"
            ).format(schema),
            (DEMO_EMAIL, password_hash(DEMO_PASSWORD)),
        )
        conn.execute(
            sql.SQL(
                "INSERT INTO {}.chat_policies(username,enabled,limit_enabled,token_limit,window_seconds) VALUES(%s,true,true,%s,%s) ON CONFLICT(username) DO NOTHING"
            ).format(schema),
            (DEMO_EMAIL, TOKEN_LIMIT, WINDOW_SECONDS),
        )
        policy = conn.execute(
            sql.SQL(
                "SELECT limit_enabled,token_limit,window_seconds FROM {}.chat_policies WHERE username=%s"
            ).format(schema),
            (DEMO_EMAIL,),
        ).fetchone()
        conn.execute(
            sql.SQL(
                "INSERT INTO {}.chat_budgets(username,resets_at) VALUES(%s,%s) ON CONFLICT(username) DO NOTHING"
            ).format(schema),
            (DEMO_EMAIL, now + timedelta(seconds=policy[2])),
        )
    print(
        json.dumps(
            {
                "user": DEMO_EMAIL,
                "role": "user",
                "token_limit": policy[1],
                "window_seconds": policy[2],
                "enforcement": policy[0],
                "existing_usage_preserved": True,
            }
        )
    )


if __name__ == "__main__":
    provision()
