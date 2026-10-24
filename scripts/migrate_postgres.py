"""Copy a verified source snapshot into one unused schema; never reset shared DBs.

Run from a workstation, not a cloud release/one-off dyno. Source and target URLs
are environment variables; credentials are never accepted as command arguments.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from sqlglot import parse_one, exp
from app.local_auth import password_hash

ARTIFACT_NAMES = [
    "catalog.json",
    "manifest.json",
    "ranking_manifest.json",
    "translations.json",
    "few_shots.json",
    "ontology.ttl",
]


def fingerprint(conn, schema, table):
    digest = hashlib.sha256()
    with conn.cursor().copy(
        sql.SQL("COPY (SELECT * FROM {}.{} ORDER BY row_key) TO STDOUT").format(
            sql.Identifier(schema), sql.Identifier(table)
        )
    ) as copy:
        for block in copy:
            digest.update(block)
    return digest.hexdigest()


def migrate(source_url, target_url, schema, artifacts, username, password):
    if (
        not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema)
        or schema in {"public", "data", "control"}
        or schema.startswith("pg_")
    ):
        raise ValueError("Choose a new application schema")
    if not username or not password or len(password) < 12:
        raise ValueError(
            "Shared administrator credentials are required (password minimum 12 characters)"
        )
    catalog = json.loads((artifacts / "catalog.json").read_text())
    tables = sorted(catalog["tables"])
    report = {
        "schema": schema,
        "source_tables": {},
        "unrelated_objects_preserved": False,
    }
    with (
        psycopg.connect(source_url, row_factory=dict_row) as source,
        psycopg.connect(target_url, row_factory=dict_row) as target,
    ):
        source.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        source.execute("SET LOCAL TIME ZONE 'UTC'")
        target.execute("SET LOCAL TIME ZONE 'UTC'")
        target.execute("SET LOCAL lock_timeout='5s'")
        # Serializes this application's provisioning; an existing schema is a hard stop.
        target.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))", ("coa-migrate:" + schema,)
        )
        if target.execute(
            "SELECT 1 FROM pg_namespace WHERE nspname=%s", (schema,)
        ).fetchone():
            raise ValueError("Target namespace already exists; no objects changed")
        before = target.execute(
            "SELECT n.nspname,c.relname,c.relkind,c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' ORDER BY c.oid"
        ).fetchall()
        target.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        target.execute(
            sql.SQL("REVOKE ALL ON SCHEMA {} FROM PUBLIC").format(
                sql.Identifier(schema)
            )
        )
        for table in tables:
            columns = source.execute(
                "SELECT a.attname,format_type(a.atttypid,a.atttypmod) AS type,a.attnotnull,pg_get_expr(d.adbin,d.adrelid) AS default_expr FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum WHERE n.nspname='data' AND c.relname=%s AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum",
                (table,),
            ).fetchall()
            if not columns or "row_key" not in {col["attname"] for col in columns}:
                raise ValueError("Source catalog/table mismatch")
            parts = []
            for col in columns:
                # Defaults on these fixed source tables are absent; fail closed if changed.
                if col["default_expr"] is not None:
                    raise ValueError(
                        "Unexpected source default; explicit migration required"
                    )
                parts.append(
                    sql.SQL("{} {} {}").format(
                        sql.Identifier(col["attname"]),
                        sql.SQL(col["type"]),
                        sql.SQL("NOT NULL" if col["attnotnull"] else ""),
                    )
                )
            constraints = source.execute(
                "SELECT con.contype, ARRAY(SELECT attname FROM unnest(con.conkey) WITH ORDINALITY k(attnum,ord) JOIN pg_attribute a ON a.attrelid=con.conrelid AND a.attnum=k.attnum ORDER BY k.ord) AS columns FROM pg_constraint con WHERE con.conrelid=to_regclass(%s) AND con.contype IN ('p','u')",
                ("data." + table,),
            ).fetchall()
            for con in constraints:
                parts.append(
                    sql.SQL("{} ({})").format(
                        sql.SQL("PRIMARY KEY" if con["contype"] == "p" else "UNIQUE"),
                        sql.SQL(",").join(map(sql.Identifier, con["columns"])),
                    )
                )
            target.execute(
                sql.SQL("CREATE TABLE {}.{} ({})").format(
                    sql.Identifier(schema),
                    sql.Identifier(table),
                    sql.SQL(",").join(parts),
                )
            )
            with source.cursor().copy(
                sql.SQL("COPY {}.{} TO STDOUT").format(
                    sql.Identifier("data"), sql.Identifier(table)
                )
            ) as out:
                with target.cursor().copy(
                    sql.SQL("COPY {}.{} FROM STDIN").format(
                        sql.Identifier(schema), sql.Identifier(table)
                    )
                ) as incoming:
                    buffer = bytearray()
                    for block in out:
                        buffer.extend(block)
                        if len(buffer) >= 1024 * 1024:
                            incoming.write(buffer)
                            buffer.clear()
                    if buffer:
                        incoming.write(buffer)
            indexes = source.execute(
                "SELECT i.indexrelid,am.amname,idx.relname,i.indnkeyatts,i.indnatts,pg_get_expr(i.indpred,i.indrelid) AS predicate FROM pg_index i JOIN pg_class idx ON idx.oid=i.indexrelid JOIN pg_am am ON am.oid=idx.relam WHERE i.indrelid=to_regclass(%s) AND NOT EXISTS(SELECT 1 FROM pg_constraint con WHERE con.conindid=i.indexrelid)",
                ("data." + table,),
            ).fetchall()
            for idx in indexes:
                keys = [
                    source.execute(
                        "SELECT pg_get_indexdef(%s,%s,true) AS key",
                        (idx["indexrelid"], n),
                    ).fetchone()["key"]
                    for n in range(1, idx["indnatts"] + 1)
                ]
                stmt = sql.SQL("CREATE INDEX {} ON {}.{} USING {} ({})").format(
                    sql.Identifier(idx["relname"]),
                    sql.Identifier(schema),
                    sql.Identifier(table),
                    sql.Identifier(idx["amname"]),
                    sql.SQL(",").join(map(sql.SQL, keys[: idx["indnkeyatts"]])),
                )
                if idx["indnatts"] > idx["indnkeyatts"]:
                    stmt += sql.SQL(" INCLUDE ({})").format(
                        sql.SQL(",").join(map(sql.SQL, keys[idx["indnkeyatts"] :]))
                    )
                if idx["predicate"]:
                    stmt += sql.SQL(" WHERE ") + sql.SQL(idx["predicate"])
                target.execute(stmt)
            count = source.execute(
                sql.SQL("SELECT count(*) AS n FROM data.{}").format(
                    sql.Identifier(table)
                )
            ).fetchone()["n"]
            actual = target.execute(
                sql.SQL("SELECT count(*) AS n FROM {}.{}").format(
                    sql.Identifier(schema), sql.Identifier(table)
                )
            ).fetchone()["n"]
            expected_hash = fingerprint(source, "data", table)
            if count != actual or expected_hash != fingerprint(target, schema, table):
                raise ValueError("Count/content verification failed")
            report["source_tables"][table] = {"rows": actual, "sha256": expected_hash}
            print("Verified source table:", table, actual, flush=True)
        for rel in catalog["relationships"]:
            if rel["status"] == "validated":
                target.execute(
                    sql.SQL(
                        "ALTER TABLE {}.{} ADD FOREIGN KEY ({}) REFERENCES {}.{} ({})"
                    ).format(
                        *map(
                            sql.Identifier,
                            [
                                schema,
                                rel["source"],
                                rel["column"],
                                schema,
                                rel["target"],
                                rel["target_column"],
                            ],
                        )
                    )
                )
        # Statements are scoped structurally, not replaced inside data or dump text.
        ddl = (
            Path(__file__).resolve().parents[1] / "infra/heroku/control.sql"
        ).read_text()
        for statement in ddl.split(";"):
            if statement.strip():
                target.execute(sql.SQL(statement).format(schema=sql.Identifier(schema)))
        accounts = source.execute("SELECT * FROM control.accounts").fetchall()
        for row in accounts:
            target.execute(
                sql.SQL(
                    "INSERT INTO {}.auth_accounts(username,role,password_hash,enabled) VALUES(%s,%s,%s,%s)"
                ).format(sql.Identifier(schema)),
                (
                    row["username"],
                    row["role"],
                    password_hash(password)
                    if row["username"] == username
                    else row["password_hash"],
                    row["enabled"],
                ),
            )
            target.execute(
                sql.SQL(
                    "INSERT INTO {}.chat_policies(username,enabled,limit_enabled,token_limit,window_seconds) VALUES(%s,%s,%s,%s,%s)"
                ).format(sql.Identifier(schema)),
                (
                    row["username"],
                    row["enabled"],
                    False,
                    row["token_limit"],
                    row["window_seconds"],
                ),
            )
        if username not in {r["username"] for r in accounts}:
            raise ValueError("Configured administrator does not match source identity")
        for old, new in [
            ("budgets", "chat_budgets"),
            ("reservations", "chat_reservations"),
            ("jobs", "chat_jobs"),
        ]:
            with source.cursor().copy(
                sql.SQL("COPY control.{} TO STDOUT").format(sql.Identifier(old))
            ) as out:
                with target.cursor().copy(
                    sql.SQL("COPY {}.{} FROM STDIN").format(
                        sql.Identifier(schema), sql.Identifier(new)
                    )
                ) as incoming:
                    for block in out:
                        incoming.write(block)
        target.execute(
            sql.SQL(
                "UPDATE {}.chat_jobs SET state='expired' WHERE state='running'"
            ).format(sql.Identifier(schema))
        )
        # Prior browser sessions and throttles remain in the verified source backup.
        for name in ARTIFACT_NAMES:
            payload = (
                (artifacts / name).read_text()
                if name.endswith(".ttl")
                else json.loads((artifacts / name).read_text())
            )
            if name == "few_shots.json":
                for example in payload:
                    tree = parse_one(example["sql"], read="postgres")
                    for table in tree.find_all(exp.Table):
                        if table.db == "data":
                            table.set("db", exp.Identifier(this=schema))
                    example["sql"] = tree.sql(dialect="postgres")
            target.execute(
                sql.SQL(
                    "INSERT INTO {}.ontology_versions(name,version,payload) VALUES(%s,%s,%s)"
                ).format(sql.Identifier(schema)),
                (name, catalog["version"], Jsonb(payload)),
            )
            if name == "few_shots.json":
                for number, example in enumerate(payload):
                    target.execute(
                        sql.SQL(
                            "INSERT INTO {}.ontology_examples(id,payload) VALUES(%s,%s)"
                        ).format(sql.Identifier(schema)),
                        (number, Jsonb(example)),
                    )
                    migrated_rows = target.execute(example["sql"]).fetchall()
                    original = json.loads((artifacts / name).read_text())[number]
                    if migrated_rows != source.execute(original["sql"]).fetchall():
                        raise ValueError("Few-shot query parity failed")
        after = target.execute(
            "SELECT n.nspname,c.relname,c.relkind,c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT LIKE 'pg_%%' AND n.nspname<>'information_schema' AND n.nspname<>%s ORDER BY c.oid",
            (schema,),
        ).fetchall()
        if before != after:
            raise ValueError("Unrelated database objects changed")
        report["unrelated_objects_preserved"] = True
        report["few_shots_verified"] = 5
        report["source_accounts"] = len(accounts)
        report["token_enforcement"] = False
        report["usage_ledger_preserved"] = True
        report["version"] = catalog["version"]
        report["completed_at"] = datetime.now(timezone.utc).isoformat()
        target.execute(
            sql.SQL(
                "INSERT INTO {}.migration_ledger(version,report) VALUES(%s,%s)"
            ).format(sql.Identifier(schema)),
            ("001", Jsonb(report)),
        )
        for table in tables:
            target.execute(
                sql.SQL("ANALYZE {}.{}").format(
                    sql.Identifier(schema), sql.Identifier(table)
                )
            )
    return report


def main():
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    try:
        from urllib.parse import urlsplit

        target_url = os.environ["MIGRATION_DATABASE_URL"]
        if urlsplit(target_url).hostname not in {"127.0.0.1", "localhost"}:
            backup_path = (
                Path(__file__).resolve().parents[1]
                / ".deploy/target-backup-current.json"
            )
            backup = json.loads(backup_path.read_text())
            age = datetime.now(timezone.utc) - datetime.fromisoformat(
                backup["created_at"]
            )
            if (
                backup.get("status") != "passed"
                or not backup.get("isolated_restore")
                or backup.get("target_fingerprint")
                != hashlib.sha256(target_url.encode()).hexdigest()
                or age.total_seconds() > 86400
            ):
                raise ValueError("A current verified backup of this target is required")
        report = migrate(
            os.environ["SOURCE_DATABASE_URL"],
            target_url,
            os.getenv("DATA_SCHEMA", "coa_puroseka"),
            Path(os.environ["SOURCE_ARTIFACT_DIR"]),
            os.environ["ADMIN_USERNAME"].strip().lower(),
            os.environ["ADMIN_PASSWORD"],
        )
    except Exception as exc:
        print(
            "Migration stopped; uncommitted target changes rolled back. Error type:",
            type(exc).__name__,
            file=sys.stderr,
        )
        return 1
    p = Path(".deploy/migration-verification.json")
    p.parent.mkdir(exist_ok=True, mode=0o700)
    p.write_text(json.dumps(report, indent=2) + "\n")
    p.chmod(0o600)
    print(
        "Migration committed; full table fingerprints and unrelated-object inventory verified."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
