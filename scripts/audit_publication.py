"""Audit staged source and every reachable history blob before publishing.

Findings contain paths and reasons only. Private reference configuration can be
supplied through PRIVATE_REFERENCE_ENV; its values never appear in the report.
"""

import configparser
import json
import netrc
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit, unquote

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {
    ".gitignore",
    ".dockerignore",
    ".slugignore",
    ".env.example",
    ".python-version",
    "README.md",
    "LICENSE",
    "NOTICE",
    "THIRD_PARTY.md",
    "Dockerfile",
    "Makefile",
    "compose.yml",
    "pyproject.toml",
    "requirements.txt",
    "Procfile",
    "app.json",
    "package.json",
    "package-lock.json",
}
ROOTS = {
    "app",
    "web",
    "infra",
    "scripts",
    "semantic",
    "tests",
    "docs",
    "shared",
    "services",
}
JSON_FILES = {
    "app.json",
    "package.json",
    "package-lock.json",
    "semantic/definitions.json",
    "semantic/rankings.json",
    "semantic/few_shots.json",
    "web/package.json",
    "web/package-lock.json",
    "web/tsconfig.json",
}


def configured_secrets():
    values = set()
    for profile in (Path.home() / ".config/cloudflare/config").glob("*.json"):
        data = json.loads(profile.read_text())
        for key in ("oauth_token", "refresh_token"):
            if data.get(key):
                values.add(data[key])
    for path in [
        ROOT / ".env",
        Path(os.environ.get("PRIVATE_REFERENCE_ENV", str(ROOT / ".env"))),
    ]:
        for key, value in dotenv_values(path).items():
            if not value:
                continue
            if (
                any(
                    s in key.upper() for s in ("API_KEY", "PASSWORD", "SECRET", "TOKEN")
                )
                and len(value) >= 8
            ):
                values.add(value)
            if key.endswith("DATABASE_URL"):
                values.add(value)
                password = urlsplit(value).password
                if password:
                    values.add(unquote(password))
    credentials = Path.home() / ".aws/credentials"
    if credentials.exists():
        parser = configparser.RawConfigParser()
        parser.read(credentials)
        for section in parser.sections():
            for key, value in parser.items(section):
                if (
                    any(s in key for s in ("access_key", "session_token"))
                    and len(value) >= 8
                ):
                    values.add(value)
    try:
        auth = netrc.netrc().authenticators("api.heroku.com")
        if auth:
            values.add(auth[2])
    except (OSError, netrc.NetrcParseError):
        pass
    return values


def audit():
    secrets = configured_secrets()
    findings = []
    items = set()
    for line in subprocess.check_output(
        ["git", "ls-files", "-s"], cwd=ROOT, text=True
    ).splitlines():
        meta, path = line.split("\t", 1)
        items.add((meta.split()[1], path))
    history = subprocess.run(
        ["git", "rev-list", "--objects", "--all"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    for line in history.stdout.splitlines():
        oid, _, path = line.partition(" ")
        if (
            subprocess.check_output(
                ["git", "cat-file", "-t", oid], cwd=ROOT, text=True
            ).strip()
            == "blob"
        ):
            items.add((oid, path))
    for oid, path in items:
        p = Path(path)
        if path not in ROOT_FILES and p.parts[0] not in ROOTS:
            findings.append((path, "outside source allowlist"))
        if p.suffix == ".json" and path not in JSON_FILES:
            findings.append((path, "unexpected JSON artifact"))
        if p.suffix in {
            ".gz",
            ".csv",
            ".tsv",
            ".jsonl",
            ".parquet",
            ".dump",
            ".backup",
            ".db",
            ".sqlite",
            ".zip",
            ".pem",
            ".key",
            ".pyc",
            ".png",
        }:
            findings.append((path, "private data or binary format"))
        raw = subprocess.check_output(["git", "cat-file", "blob", oid], cwd=ROOT)
        try:
            text = raw.decode()
        except UnicodeDecodeError:
            findings.append((path, "non-text file"))
            continue
        if any(value in text for value in secrets):
            findings.append((path, "configured secret value"))
        if re.search(
            r"(?:AKIA|ASIA)[A-Z0-9]{16}|gh[pousr]_[A-Za-z0-9]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
            text,
        ):
            findings.append((path, "credential pattern"))
        if len(raw) > 300000:
            findings.append((path, "unexpected large blob"))
    report = {
        "status": "failed" if findings else "passed",
        "source_paths_scanned": len({p for _, p in items}),
        "blobs_scanned": len(items),
        "secrets_found": bool(findings),
        "findings": sorted(set(findings)),
    }
    output = ROOT / "artifacts/publication-audit.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    output.chmod(0o600)
    print(json.dumps(report))
    return not findings


if __name__ == "__main__":
    raise SystemExit(0 if audit() else 1)
