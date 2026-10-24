"""Deploy only audited static web/dist assets to Cloudflare Pages.

cf manages projects, deployment creation, custom domains and DNS. The installed
CLI has no asset-upload command; its short-lived upload JWT authorizes the three
Pages asset endpoints used below. OAuth tokens are never read or copied.
"""

import base64
import hashlib
import json
import mimetypes
from pathlib import Path
import subprocess
import sys
import time

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.audit_publication import configured_secrets
from scripts.cloudflare_cli import cf, zone, dns_only, PROJECT, FRONTEND, BACKEND

ROOT = Path(__file__).resolve().parents[1]


def deploy():
    dist = ROOT / "web/dist"
    secrets = configured_secrets()
    assets = {}
    special = {}
    for path in sorted(dist.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(dist).as_posix()
        if (
            path.is_symlink()
            or path.suffix
            not in {
                ".html",
                ".js",
                ".css",
                ".svg",
                ".png",
                ".ico",
                ".webp",
                ".woff",
                ".woff2",
                ".ttf",
            }
            and relative not in {"_headers", "_redirects"}
        ):
            raise ValueError("Unexpected frontend artifact: " + relative)
        content = path.read_bytes()
        if any(secret.encode() in content for secret in secrets):
            raise ValueError(
                "Private configuration found in frontend artifact: " + relative
            )
        if relative in {"_headers", "_redirects"}:
            special[relative] = content.decode()
            continue
        # Asset keys are content-addressed 128-bit hexadecimal identifiers.
        key = hashlib.sha256(path.suffix.encode() + b"\0" + content).hexdigest()[:32]
        assets["/" + relative] = {
            "key": key,
            "value": base64.b64encode(content).decode(),
            "base64": True,
            "metadata": {
                "contentType": mimetypes.guess_type(path.name)[0]
                or "application/octet-stream"
            },
        }
    if "/index.html" not in assets or not any(
        ("https://" + BACKEND).encode() in p.read_bytes()
        for p in dist.glob("assets/*.js")
    ):
        raise ValueError("Build the frontend with its production VITE_API_ORIGIN first")
    z = zone()
    account = z["account"]["id"]
    projects = cf("pages", "projects", "list", account=account)
    if not any(p["name"] == PROJECT for p in projects):
        cf(
            "pages",
            "projects",
            "create",
            account=account,
            body={"name": PROJECT, "production_branch": "main"},
        )
    jwt = cf("pages", "projects", "get-upload-token", PROJECT, account=account)["jwt"]
    hashes = [a["key"] for a in assets.values()]
    with httpx.Client(
        base_url="https://api.cloudflare.com/client/v4/pages/assets",
        headers={"Authorization": "Bearer " + jwt},
        timeout=60,
    ) as client:

        def post(path, payload):
            r = client.post(path, json=payload)
            r.raise_for_status()
            data = r.json()
            if not data.get("success"):
                raise RuntimeError("Pages asset operation failed: " + path)
            return data.get("result")

        missing = set(post("/check-missing", {"hashes": hashes}))
        payload = [a for a in assets.values() if a["key"] in missing]
        if payload:
            post("/upload", payload)
        post("/upsert-hashes", {"hashes": hashes})
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    deployment = cf(
        "pages",
        "projects",
        "deployments",
        "create",
        PROJECT,
        account=account,
        body={
            "branch": "main",
            "commit_hash": revision,
            "commit_dirty": "false",
            "manifest": json.dumps({k: v["key"] for k, v in assets.items()}),
            **special,
        },
    )
    print(
        json.dumps(
            {"project": PROJECT, "deployment": deployment["id"], "assets": len(assets)}
        ),
        flush=True,
    )
    for _ in range(60):
        deployment = cf(
            "pages",
            "projects",
            "deployments",
            "get",
            deployment["id"],
            "--project-name",
            PROJECT,
            account=account,
        )
        status = deployment["latest_stage"]["status"]
        if status == "success":
            break
        if status in {"failure", "canceled"}:
            raise RuntimeError("Pages deployment did not succeed")
        time.sleep(3)
    else:
        raise RuntimeError("Pages deployment still pending")
    dns = dns_only(z, FRONTEND, PROJECT + ".pages.dev")
    domains = cf("pages", "projects", "domains", "list", PROJECT, account=account)
    if not any(d["name"] == FRONTEND for d in domains):
        cf(
            "pages",
            "projects",
            "domains",
            "create",
            PROJECT,
            account=account,
            body={"name": FRONTEND},
        )
    state = {
        "project": PROJECT,
        "account_id": account,
        "zone_id": z["id"],
        "frontend_origin": "https://" + FRONTEND,
        "api_origin": "https://" + BACKEND,
        "deployment_id": deployment["id"],
        "revision": revision,
        "dns_proxied": dns["proxied"],
        "assets": len(assets),
    }
    path = ROOT / ".deploy/cloudflare-pages.json"
    path.parent.mkdir(exist_ok=True, mode=0o700)
    path.write_text(json.dumps(state, indent=2) + "\n")
    path.chmod(0o600)
    print(
        json.dumps({"frontend": state["frontend_origin"], "dns_proxied": False}),
        flush=True,
    )


if __name__ == "__main__":
    deploy()
