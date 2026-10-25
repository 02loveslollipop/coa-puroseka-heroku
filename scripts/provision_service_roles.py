"""Create schema-scoped runtime logins on the existing database, from a workstation.

Operator credentials never enter Heroku. Generated passwords stay in ignored .env.
Reruns reuse those passwords and refuse to adopt unrelated existing roles.
"""

import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
from urllib.parse import quote, urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import dotenv_values
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ("ontology", "login", "chat")
SOURCE_TABLES = (
    "cards",
    "events",
    "gachas",
    "characters",
    "event_cards",
    "entity_labels",
    "event_rewards",
    "gacha_details",
    "gacha_pickups",
    "card_parameters",
    "gacha_behaviors",
    "gacha_rarity_rates",
    "card_training_costs",
    "event_reward_ranges",
    "ranking_observations",
    "card_master_resources",
)
# Each verb is limited to the columns actually written by its service.
POLICIES = {
    "login": {
        "auth_accounts": {"SELECT": None, "UPDATE": ("password_hash",)},
        "auth_sessions": {
            "SELECT": None,
            "INSERT": ("token_hash", "username", "csrf", "expires_at"),
            "DELETE": None,
        },
        "auth_login_attempts": {
            "SELECT": None,
            "INSERT": ("key", "attempts", "resets_at"),
            "UPDATE": ("attempts", "resets_at"),
        },
    },
    "chat": {
        "chat_policies": {
            "SELECT": None,
            "UPDATE": ("limit_enabled", "token_limit", "window_seconds"),
        },
        "chat_budgets": {
            "SELECT": None,
            "UPDATE": ("used_tokens", "reserved_tokens", "started_at", "resets_at"),
        },
        "chat_reservations": {
            "SELECT": None,
            "INSERT": ("id", "username", "amount"),
            "UPDATE": ("state", "charged_tokens"),
            "DELETE": None,
        },
        "chat_jobs": {
            "SELECT": None,
            "INSERT": ("id", "username", "request"),
            "UPDATE": ("state", "request", "result"),
            "DELETE": None,
        },
    },
    "ontology": {t: {"SELECT": None} for t in (*SOURCE_TABLES, "ontology_versions")},
}


def role_name(schema, service):
    name = schema + "_" + service
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name) or schema.startswith("pg_"):
        raise ValueError("Invalid application schema/role name")
    return name


def service_database(cfg, service):
    """Deployments fail closed instead of falling back to a shared/operator login."""
    value = cfg.get(service.upper() + "_DATABASE_URL")
    if not value:
        raise ValueError("Provision a separate database role for every service first")
    params = conninfo_to_dict(value)
    if params.get("user") != role_name(cfg.get("DATA_SCHEMA", "coa_puroseka"), service):
        raise ValueError("Service must use its dedicated runtime role")
    if params.get("sslmode") != "require":
        raise ValueError("Runtime database connections must require TLS")
    return value


def database_url(base, role, password):
    host = base["host"]
    if ":" in host:
        host = "[" + host + "]"
    return (
        "postgresql://"
        + quote(role, safe="")
        + ":"
        + quote(password, safe="")
        + "@"
        + host
        + ":"
        + base.get("port", "5432")
        + "/"
        + quote(base["dbname"], safe="")
        + "?"
        + urlencode({"sslmode": base["sslmode"]})
    )


def apply_grants(conn, schema, role, service):
    """Replace direct grants, including old column grants, with this policy."""
    ident = sql.Identifier
    for kind in ("TABLES", "SEQUENCES", "FUNCTIONS"):
        conn.execute(
            sql.SQL("REVOKE ALL ON ALL {} IN SCHEMA {} FROM {}").format(
                sql.SQL(kind), ident(schema), ident(role)
            )
        )
    columns_by_table = {}
    for table, column in conn.execute(
        "SELECT table_name,column_name FROM information_schema.columns WHERE table_schema=%s",
        (schema,),
    ).fetchall():
        columns_by_table.setdefault(table, []).append(column)
    for table, columns in columns_by_table.items():
        conn.execute(
            sql.SQL("REVOKE ALL ({}) ON {}.{} FROM {}").format(
                sql.SQL(",").join(map(ident, columns)),
                ident(schema),
                ident(table),
                ident(role),
            )
        )
    conn.execute(
        sql.SQL("REVOKE ALL ON SCHEMA {} FROM {}").format(ident(schema), ident(role))
    )
    conn.execute(
        sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(ident(schema), ident(role))
    )
    for table, grants in POLICIES[service].items():
        for verb, columns in grants.items():
            permission = sql.SQL(verb)
            if columns:
                permission += sql.SQL(" ({})").format(
                    sql.SQL(",").join(map(ident, columns))
                )
            conn.execute(
                sql.SQL("GRANT {} ON {}.{} TO {}").format(
                    permission, ident(schema), ident(table), ident(role)
                )
            )


def check_role(conn, schema, role):
    """Refuse to reuse an unmanaged role or one with ownership/memberships."""
    row = conn.execute(
        "SELECT oid,shobj_description(oid,'pg_authid'),rolsuper,rolcreaterole,"
        "rolcreatedb,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=%s",
        (role,),
    ).fetchone()
    if not row:
        return False
    if row[1] != "COA runtime role: " + schema:
        raise ValueError("Existing role is not managed by this provisioner")
    if any(row[2:]):
        raise ValueError(
            "Existing runtime role has unexpected administrative attributes"
        )
    if conn.execute(
        "SELECT 1 FROM pg_auth_members WHERE member=%s LIMIT 1", (row[0],)
    ).fetchone():
        raise ValueError("Runtime role has unexpected role memberships")
    if conn.execute(
        "SELECT 1 FROM pg_shdepend WHERE refclassid='pg_authid'::regclass "
        "AND refobjid=%s AND deptype='o' LIMIT 1",
        (row[0],),
    ).fetchone():
        raise ValueError("Runtime role unexpectedly owns objects")
    return True


def configure_role(conn, schema, service, password):
    role = role_name(schema, service)
    ident = sql.Identifier
    exists = check_role(conn, schema, role)
    verb = "ALTER" if exists else "CREATE"
    # Encrypt client-side so plaintext passwords never enter a SQL statement or
    # a provider's statement log. The resulting verifier is still kept private.
    encrypted = conn.pgconn.encrypt_password(
        password.encode(), role.encode(), b"scram-sha-256"
    ).decode()
    flags = "" if exists else "NOSUPERUSER NOREPLICATION NOBYPASSRLS "
    conn.execute(
        sql.SQL(
            "{} ROLE {} LOGIN {}NOCREATEDB NOCREATEROLE NOINHERIT "
            "CONNECTION LIMIT 8 PASSWORD {}"
        ).format(sql.SQL(verb), ident(role), sql.SQL(flags), sql.Literal(encrypted))
    )
    conn.execute(
        sql.SQL("COMMENT ON ROLE {} IS {}").format(
            ident(role), sql.Literal("COA runtime role: " + schema)
        )
    )
    conn.execute(sql.SQL("ALTER ROLE {} RESET ALL").format(ident(role)))
    defaults = {
        "search_path": "pg_catalog",
        "statement_timeout": "15s",
        "lock_timeout": "1s",
        "idle_in_transaction_session_timeout": "30s",
        "default_transaction_read_only": "on" if service == "ontology" else "off",
    }
    for key, value in defaults.items():
        conn.execute(
            sql.SQL("ALTER ROLE {} SET {} TO {}").format(
                ident(role), ident(key), sql.Literal(value)
            )
        )
    database = conn.execute("SELECT current_database()").fetchone()[0]
    conn.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            ident(database), ident(role)
        )
    )
    apply_grants(conn, schema, role, service)
    return role


def save_env(path, values):
    """Atomically preserve unrelated configuration and never print credentials."""
    lines = path.read_text().splitlines()
    keys = set(values)
    lines = [line for line in lines if line.split("=", 1)[0].strip() not in keys]
    lines += [key + "=" + json.dumps(value) for key, value in values.items()]
    fd, temporary = tempfile.mkstemp(prefix=".coa-env-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write("\n".join(lines) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def provision():
    path = ROOT / ".env"
    cfg = dotenv_values(path)
    schema = cfg.get("DATA_SCHEMA", "coa_puroseka")
    operator = conninfo_to_dict(cfg["MIGRATION_ADMIN_DATABASE_URL"])
    if operator.get("sslmode") != "require":
        raise ValueError("The Northflank operator connection must require TLS")
    # Copy only connection routing/TLS options, never operator identity or options.
    base = {
        key: operator[key]
        for key in ("host", "port", "dbname", "sslmode")
        if key in operator
    }
    values, passwords = {}, {}
    with psycopg.connect(
        cfg["MIGRATION_ADMIN_DATABASE_URL"], connect_timeout=10
    ) as conn:
        if not conn.execute(
            "SELECT nspowner=(SELECT oid FROM pg_roles WHERE rolname=current_user) "
            "FROM pg_namespace WHERE nspname=%s",
            (schema,),
        ).fetchone()[0]:
            raise ValueError("Operator must own the existing application schema")
        # No inherited PUBLIC grants may undermine separation inside our schema.
        if conn.execute(
            "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "CROSS JOIN LATERAL aclexplode(c.relacl) a "
            "WHERE n.nspname=%s AND a.grantee=0 LIMIT 1",
            (schema,),
        ).fetchone():
            raise ValueError("Application objects have unexpected PUBLIC grants")
        for service in SERVICES:
            key, role = service.upper() + "_DATABASE_URL", role_name(schema, service)
            exists = check_role(conn, schema, role)
            previous = conninfo_to_dict(cfg[key]) if cfg.get(key) else {}
            if exists and not previous:
                raise ValueError(
                    "Managed role exists but its saved credential is missing"
                )
            if previous and any(
                previous.get(k) != v for k, v in {**base, "user": role}.items()
            ):
                raise ValueError(
                    "Existing service URL does not match the intended role/database"
                )
            passwords[service] = previous.get("password") or secrets.token_urlsafe(48)
            values[key] = database_url(base, role, passwords[service])
        # Persist recovery credentials before committing CREATE ROLE. A failed
        # transaction can be rerun using these same credentials.
        save_env(path, values)
        conn.execute("SET LOCAL password_encryption='scram-sha-256'")
        for service in SERVICES:
            configure_role(conn, schema, service, passwords[service])
    for service in SERVICES:
        with psycopg.connect(
            values[service.upper() + "_DATABASE_URL"], connect_timeout=10
        ) as conn:
            assert conn.execute("SELECT current_user").fetchone()[0] == role_name(
                schema, service
            )
            assert conn.pgconn.ssl_in_use
    print(
        json.dumps(
            {
                "service_roles": list(SERVICES),
                "tls_verified": True,
                "credentials_saved_locally": True,
                "heroku_changed": False,
            }
        )
    )


if __name__ == "__main__":
    try:
        provision()
    except Exception as exc:
        # PostgreSQL errors can include connection details or password-bearing SQL.
        print("Role provisioning halted: " + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
