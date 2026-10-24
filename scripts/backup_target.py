"""Read-only target backup plus an isolated restore before shared-schema import."""

import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit, parse_qs, unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import dotenv_values
import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]


def backup(url):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = ROOT / ".deploy/backups" / ("external-target-" + stamp)
    folder.mkdir(parents=True, mode=0o700)
    dump = folder / "before-migration.dump"
    report = {
        "status": "incomplete",
        "target_fingerprint": hashlib.sha256(url.encode()).hexdigest(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with psycopg.connect(url, connect_timeout=10) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout='120s'")
        major = int(conn.execute("SHOW server_version_num").fetchone()[0]) // 10000
        snapshot = conn.execute("SELECT pg_export_snapshot()").fetchone()[0]
        tables = conn.execute(
            "SELECT schemaname,tablename FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema') AND schemaname NOT LIKE 'pg_toast%' ORDER BY 1,2"
        ).fetchall()
        counts = {}
        for schema, table in tables:
            counts[schema + "." + table] = conn.execute(
                sql.SQL("SELECT count(*) FROM {}.{}").format(
                    sql.Identifier(schema), sql.Identifier(table)
                )
            ).fetchone()[0]
        image = "postgres:" + str(major) + "-alpine"
        # Pull is local tooling; no cloud infrastructure is provisioned.
        subprocess.run(
            ["docker", "pull", image],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        p = urlsplit(url)
        env = {
            **os.environ,
            "PGHOST": p.hostname,
            "PGPORT": str(p.port or 5432),
            "PGUSER": unquote(p.username),
            "PGPASSWORD": unquote(p.password),
            "PGDATABASE": unquote(p.path[1:]),
            "PGSSLMODE": parse_qs(p.query).get("sslmode", ["require"])[0],
            "PGCONNECT_TIMEOUT": "10",
        }
        command = ["docker", "run", "--rm", "--network", "host"]
        for name in [
            "PGHOST",
            "PGPORT",
            "PGUSER",
            "PGPASSWORD",
            "PGDATABASE",
            "PGSSLMODE",
            "PGCONNECT_TIMEOUT",
        ]:
            command.extend(["--env", name])
        command.extend(
            [
                image,
                "pg_dump",
                "--format=custom",
                "--compress=6",
                "--no-owner",
                "--no-acl",
                "--snapshot=" + snapshot,
            ]
        )
        with dump.open("wb") as out:
            dump.chmod(0o600)
            result = subprocess.run(
                command, env=env, stdout=out, stderr=subprocess.PIPE
            )
        if result.returncode:
            raise RuntimeError(
                "Target pg_dump failed; existing objects not fully backed up"
            )
        report.update(
            server_major=major,
            table_counts=counts,
            database_bytes=conn.execute(
                "SELECT pg_database_size(current_database())"
            ).fetchone()[0],
        )
    report["dump_sha256"] = hashlib.file_digest(dump.open("rb"), "sha256").hexdigest()
    name = "coa-target-restore-" + secrets.token_hex(6)
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "--name",
                name,
                "--network",
                "none",
                "--env",
                "POSTGRES_PASSWORD=local-restore-verification-only",
                "--detach",
                image,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        import time

        for _ in range(100):
            r = subprocess.run(
                ["docker", "exec", name, "pg_isready", "-h", "127.0.0.1", "-U", "postgres"],
                capture_output=True,
            )
            if r.returncode == 0:
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("Scratch restore server not ready")
        with dump.open("rb") as incoming:
            r = subprocess.run(
                [
                    "docker",
                    "exec",
                    "-i",
                    name,
                    "pg_restore",
                    "-U",
                    "postgres",
                    "--dbname=postgres",
                    "--no-owner",
                    "--no-acl",
                    "--exit-on-error",
                ],
                stdin=incoming,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        if r.returncode:
            error = folder / "restore-error.log"
            error.write_bytes(r.stderr)
            error.chmod(0o600)
            raise RuntimeError("Target backup restore failed")
        for qualified, count in counts.items():
            schema, table = qualified.split(".", 1)
            # Quoting is structural; none of the connection credentials enter SQL.
            query = (
                sql.SQL("SELECT count(*) FROM {}.{}")
                .format(sql.Identifier(schema), sql.Identifier(table))
                .as_string()
            )
            r = subprocess.run(
                ["docker", "exec", name, "psql", "-U", "postgres", "-At", "-c", query],
                check=True,
                capture_output=True,
                text=True,
            )
            if int(r.stdout.strip()) != count:
                raise RuntimeError("Restored table count mismatch")
        report.update(
            status="passed",
            isolated_restore=True,
            restore_network="none",
            dump_bytes=dump.stat().st_size,
            folder=str(folder),
        )
    finally:
        subprocess.run(
            ["docker", "rm", "-fv", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    manifest = folder / "manifest.json"
    manifest.write_text(json.dumps(report, indent=2) + "\n")
    manifest.chmod(0o600)
    sums = folder / "SHA256SUMS"
    sums.write_text(
        report["dump_sha256"]
        + "  "
        + dump.name
        + "\n"
        + hashlib.sha256(manifest.read_bytes()).hexdigest()
        + "  manifest.json\n"
    )
    sums.chmod(0o600)
    import shutil

    share = Path("/mnt/nfs/ZeroTwoShare-1")
    if share.is_dir():
        copy = share / ("coa_" + folder.name)
        shutil.copytree(folder, copy)
        copy.chmod(0o700)
        for file in copy.iterdir():
            file.chmod(0o600)
        if (
            hashlib.file_digest((copy / dump.name).open("rb"), "sha256").hexdigest()
            != report["dump_sha256"]
        ):
            raise RuntimeError("Share checksum failed")
    current = ROOT / ".deploy/target-backup-current.json"
    current.write_text(json.dumps(report, indent=2) + "\n")
    current.chmod(0o600)
    print(
        json.dumps(
            {
                "status": "passed",
                "existing_tables_restored": len(counts),
                "dump_bytes": report["dump_bytes"],
                "private_backup_folder": str(folder),
            }
        )
    )
    return report


if __name__ == "__main__":
    try:
        backup(dotenv_values(ROOT / ".env")["MIGRATION_DATABASE_URL"])
    except Exception as exc:
        print(
            "Target backup stopped; no target writes performed. Error type:",
            type(exc).__name__,
            file=sys.stderr,
        )
        raise SystemExit(1)
