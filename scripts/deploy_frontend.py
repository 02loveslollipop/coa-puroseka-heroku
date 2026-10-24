"""Deploy only audited static web/dist assets to Cloudflare Pages.

cf manages projects, deployment status, custom domains and DNS. The installed
CLI has no asset-upload command; its short-lived upload JWT authorizes the three
Pages asset endpoints used below. Deployment metadata containing headers needs
the multipart API because cf cannot attach those files. No tokens are persisted.
"""

import base64
import hashlib
import json
import mimetypes
import os
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
    if subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=ROOT, text=True
    ).strip():
        raise ValueError("Commit and audit source before deploying the frontend")
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
    # cf --body becomes a literal "body" multipart field; its generated flags
    # cannot attach _headers/_redirects. Use the same authenticated cf session
    # for this payload, while cf manages all resource/domain/DNS operations.
    identity = cf("auth", "whoami")
    bearer = os.getenv("CLOUDFLARE_API_TOKEN")
    if not bearer:
        source = identity.get("authSource", "")
        prefix = "OAuth token from "
        if not identity.get("authenticated") or not source.startswith(prefix):
            raise RuntimeError(
                "An authenticated cf OAuth profile or API token is required"
            )
        bearer = json.loads(Path(source[len(prefix) :]).read_text())["oauth_token"]
    fields = {
        "branch": (None, "main"),
        "commit_hash": (None, revision),
        "commit_dirty": (None, "false"),
        "manifest": (None, json.dumps({k: v["key"] for k, v in assets.items()})),
        **{k: (k, v, "text/plain") for k, v in special.items()},
    }
    response = httpx.post(
        f"https://api.cloudflare.com/client/v4/accounts/{account}/pages/projects/{PROJECT}/deployments",
        headers={"Authorization": "Bearer " + bearer},
        files=fields,
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("success"):
        raise RuntimeError("Pages multipart deployment failed")
    deployment = data["result"]
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
    domains = cf(
        "pages",
        "projects",
        "domains",
        "list",
        "--project-name",
        PROJECT,
        account=account,
    )
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
