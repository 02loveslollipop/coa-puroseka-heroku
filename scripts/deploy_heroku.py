"""Create exactly three personal apps with Eco web dynos and no add-ons.

Uses Heroku app-setups so Eco sizing is explicit before the first process starts.
No release, postdeploy, worker, or one-off process is declared. Database migration
is a separate workstation operation against the existing database.
"""

import json
import netrc
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from dotenv import dotenv_values, set_key

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".deploy/heroku-services.json"
REPOSITORY = "https://github.com/02loveslollipop/coa-puroseka-heroku"


def validate_resources(manifest):
    if manifest.get("formation") != {"web": {"quantity": 1, "size": "eco"}}:
        raise ValueError("Only one Eco web dyno per service is authorized")
    if manifest.get("addons") or manifest.get("scripts"):
        raise ValueError("Add-ons and deployment scripts are not authorized")
    if (ROOT / "Procfile").read_text().strip() != "web: python infra/heroku/start.py":
        raise ValueError("Only the web process is authorized")


def save(state):
    STATE.parent.mkdir(exist_ok=True, mode=0o700)
    STATE.write_text(json.dumps(state, indent=2) + "\n")
    STATE.chmod(0o600)


def deploy():
    manifest = json.loads((ROOT / "app.json").read_text())
    validate_resources(manifest)
    cfg = dotenv_values(ROOT / ".env")
    if not cfg.get("MIGRATION_DATABASE_URL") or not cfg.get("MISTRAL_API_KEY"):
        raise ValueError("Private database/model configuration missing")
    # Never inherit AWS env vars or copy all local configuration to Heroku.
    for key in ["LOGIN_SERVICE_TOKEN", "ONTOLOGY_SERVICE_TOKEN"]:
        if not cfg.get(key):
            import secrets

            value = secrets.token_urlsafe(48)
            set_key(ROOT / ".env", key, value)
            cfg[key] = value
    (ROOT / ".env").chmod(0o600)
    token = (
        os.getenv("HEROKU_API_KEY") or netrc.netrc().authenticators("api.heroku.com")[2]
    )
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    remote = subprocess.check_output(
        ["git", "remote", "get-url", "origin"], cwd=ROOT, text=True
    ).strip()
    if remote not in {
        REPOSITORY + ".git",
        "git@github.com:02loveslollipop/coa-puroseka-heroku.git",
    }:
        raise ValueError("Unexpected Git remote; refusing deployment")
    archive = REPOSITORY + "/archive/" + revision + ".tar.gz"
    state = (
        json.loads(STATE.read_text())
        if STATE.exists()
        else {"services": {}, "dyno_type": "eco", "repository": REPOSITORY}
    )
    with httpx.Client(
        base_url="https://api.heroku.com",
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.heroku+json; version=3",
        },
        timeout=30,
    ) as api:
        for service in ["login", "ontology", "chat"]:
            base = {
                "COA_SERVICE": service,
                "DATA_SCHEMA": cfg.get("DATA_SCHEMA", "coa_puroseka"),
                "ARTIFACT_STORE": "database",
                "SEMANTIC_VERSION": "puroseka-1.1.0",
            }
            # Credentials are restricted per service where database roles are available.
            if service == "ontology":
                base.update(
                    DATABASE_URL=cfg.get("ONTOLOGY_DATABASE_URL")
                    or cfg["MIGRATION_DATABASE_URL"],
                    LOGIN_SERVICE_URL=state["services"]["login"]["url"],
                    LOGIN_SERVICE_TOKEN=cfg["LOGIN_SERVICE_TOKEN"],
                    ONTOLOGY_SERVICE_TOKEN=cfg["ONTOLOGY_SERVICE_TOKEN"],
                )
            else:
                base["CONTROL_DATABASE_URL"] = (
                    cfg.get(service.upper() + "_DATABASE_URL")
                    or cfg["MIGRATION_DATABASE_URL"]
                )
                if service == "login":
                    base["LOGIN_SERVICE_TOKEN"] = cfg["LOGIN_SERVICE_TOKEN"]
                else:
                    base.update(
                        LOGIN_SERVICE_URL=state["services"]["login"]["url"],
                        LOGIN_SERVICE_TOKEN=cfg["LOGIN_SERVICE_TOKEN"],
                        ONTOLOGY_SERVICE_URL=state["services"]["ontology"]["url"],
                        ONTOLOGY_SERVICE_TOKEN=cfg["ONTOLOGY_SERVICE_TOKEN"],
                        MISTRAL_API_KEY=cfg["MISTRAL_API_KEY"],
                        MISTRAL_MODEL="ministral-8b-latest",
                    )
            item = state["services"].get(service)
            if not item:
                response = api.post(
                    "/app-setups",
                    json={
                        "app": {"name": "coa-puroseka-" + service, "region": "us"},
                        "source_blob": {"url": archive},
                        "overrides": {"env": base},
                    },
                )
                response.raise_for_status()
                setup = response.json()
                item = {
                    "app": setup["app"]["name"],
                    "id": setup["app"]["id"],
                    "setup_id": setup["id"],
                    "revision": revision,
                    "status": setup["status"],
                }
                details = api.get("/apps/" + item["id"])
                details.raise_for_status()
                item["url"] = details.json()["web_url"].rstrip("/")
                state["services"][service] = item
                save(state)
            if service == "chat":
                base["PUBLIC_ORIGIN"] = item["url"]
            response = api.patch("/apps/" + item["id"] + "/config-vars", json=base)
            response.raise_for_status()
            last = None
            for _ in range(100):
                response = api.get("/app-setups/" + item["setup_id"])
                response.raise_for_status()
                setup = response.json()
                status = setup["status"]
                if status != last:
                    print(
                        json.dumps(
                            {
                                "service": service,
                                "setup": status,
                                "build": (setup.get("build") or {}).get("status"),
                            }
                        ),
                        flush=True,
                    )
                    last = status
                if status == "failed":
                    item["status"] = "failed"
                    save(state)
                    # API messages contain no configuration values; retain details locally.
                    error = ROOT / "artifacts/heroku-setup-error.json"
                    error.write_text(json.dumps(setup))
                    error.chmod(0o600)
                    raise RuntimeError(
                        "Eco app setup failed for " + service + "; see private report"
                    )
                if status == "succeeded":
                    break
                time.sleep(5)
            else:
                raise RuntimeError("Build still pending; resume script to monitor")
            formation = api.get("/apps/" + item["id"] + "/formation")
            formation.raise_for_status()
            if any(
                f["size"].lower() != "eco" or f["type"] != "web"
                for f in formation.json()
            ):
                # Never resize into an unauthorized class. Stop this app if an unexpected formation appears.
                for f in formation.json():
                    api.patch(
                        "/apps/" + item["id"] + "/formation/" + f["id"],
                        json={"quantity": 0},
                    ).raise_for_status()
                raise RuntimeError("Unexpected formation stopped; deployment halted")
            addons = api.get("/apps/" + item["id"] + "/addons")
            addons.raise_for_status()
            if addons.json():
                raise RuntimeError("Unexpected add-on; deployment halted")
            item["status"] = "deployed"
            item["formation"] = [
                {"type": f["type"], "size": f["size"], "quantity": f["quantity"]}
                for f in formation.json()
            ]
            save(state)
            print(
                json.dumps(
                    {"service": service, "url": item["url"], "size": "Eco", "addons": 0}
                ),
                flush=True,
            )
    return state


if __name__ == "__main__":
    try:
        deploy()
    except Exception as exc:
        print("Deployment halted:", type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
