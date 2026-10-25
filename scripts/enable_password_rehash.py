"""Grant only password-hash updates for login-time upgrades on an existing DB.

Run locally with the operator credential in the ignored project .env. No
credentials are copied to Heroku and no source-data privileges are changed.
"""

import json
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values
import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]


def grant(conn, schema, role):
    conn.execute(
        sql.SQL("GRANT UPDATE(password_hash) ON {}.auth_accounts TO {}").format(
            sql.Identifier(schema), sql.Identifier(role)
        )
    )


def run():
    cfg = dotenv_values(ROOT / ".env")
    runtime = cfg.get("LOGIN_DATABASE_URL") or cfg["MIGRATION_DATABASE_URL"]
    owner = cfg["MIGRATION_ADMIN_DATABASE_URL"]

    def target(value):
        url = urlsplit(value)
        return url.hostname, url.port or 5432, url.path

    if target(runtime) != target(owner):
        raise ValueError("Operator and login must target the same database")
    schema = cfg.get("DATA_SCHEMA", "coa_puroseka")
    with psycopg.connect(runtime, connect_timeout=8) as conn:
        role = conn.execute("SELECT current_user").fetchone()[0]
    with psycopg.connect(owner, connect_timeout=8) as conn:
        if conn.execute("SELECT current_user").fetchone()[0] == role:
            raise ValueError("The login runtime must use a separate non-admin account")
        conn.execute("SET LOCAL lock_timeout='5s'")
        grant(conn, schema, role)
    print(
        json.dumps(
            {"password_hash_update_granted": True, "dataset_privileges_changed": False}
        )
    )


if __name__ == "__main__":
    run()
