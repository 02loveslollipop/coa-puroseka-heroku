"""Cloudflare control operations use the authenticated cf CLI, never saved tokens."""

import json
import os
import subprocess

ZONE = "02labs.me"
FRONTEND = "chat.coa.puroseka.02labs.me"
BACKEND = "api.coa.puroseka.02labs.me"
PROJECT = "coa-puroseka"


def cf(*args, account=None, body=None):
    env = dict(os.environ)
    if account:
        env["CLOUDFLARE_ACCOUNT_ID"] = account
    command = ["cf", *args]
    if body is not None:
        command += ["--body", json.dumps(body)]
    result = subprocess.run(command, env=env, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError("cf " + " ".join(args[:4]) + ": " + result.stderr[:800])
    return json.loads(result.stdout)


def zone():
    matches = [z for z in cf("zones", "list", "--name", ZONE) if z["name"] == ZONE]
    if len(matches) != 1:
        raise RuntimeError("Expected exactly one accessible 02labs.me zone")
    return matches[0]


def dns_only(z, hostname, target):
    records = cf(
        "dns",
        "records",
        "list",
        "--zone",
        z["id"],
        "--name",
        hostname,
        account=z["account"]["id"],
    )
    if records:
        if (
            len(records) == 1
            and records[0]["type"] == "CNAME"
            and records[0]["content"].rstrip(".") == target.rstrip(".")
            and records[0]["proxied"] is False
        ):
            return records[0]
        raise RuntimeError(
            "Existing DNS record conflicts with " + hostname + "; left unchanged"
        )
    return cf(
        "dns",
        "records",
        "create",
        "--zone",
        z["id"],
        account=z["account"]["id"],
        body={
            "type": "CNAME",
            "name": hostname,
            "content": target,
            "ttl": 300,
            "proxied": False,
            "comment": "COA deployment; DNS only; origin manages TLS",
        },
    )
