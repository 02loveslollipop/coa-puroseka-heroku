"""Rebuild the three recorded Eco apps from service-specific audited source.

No apps, add-ons, workers, schedulers, one-off dynos or databases are created.
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
from dotenv import dotenv_values

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
    from scripts.audit_publication import audit
    from scripts.service_bundle import upload
    from scripts.provision_service_roles import service_database

    manifest = json.loads((ROOT / "app.json").read_text())
    validate_resources(manifest)
    if subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=ROOT, text=True
    ).strip():
        raise ValueError("Commit and audit source before deploying")
    if not audit():
        raise ValueError("Publication audit failed")
    cfg = dotenv_values(ROOT / ".env")
    databases = {
        service: service_database(cfg, service)
        for service in ("login", "ontology", "chat")
    }
    state = json.loads(STATE.read_text())
    if set(state["services"]) != {"login", "ontology", "chat"}:
        raise ValueError("Record the three existing Eco apps before deployment")
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
        raise ValueError("Unexpected Git remote")
    published = subprocess.check_output(
        ["git", "ls-remote", "origin", "refs/heads/main"], cwd=ROOT, text=True
    )
    if not published.startswith(revision + "\t"):
        raise ValueError("Publish the audited revision before deployment")
    token = (
        os.getenv("HEROKU_API_KEY") or netrc.netrc().authenticators("api.heroku.com")[2]
    )
    with httpx.Client(
        base_url="https://api.heroku.com",
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.heroku+json; version=3",
        },
        timeout=30,
    ) as api:
        # Validate every app before any mutation. No resources are created,
        # resized or stopped by this script.
        for item in state["services"].values():
            formation = api.get(f"/apps/{item['id']}/formation")
            formation.raise_for_status()
            if len(formation.json()) != 1 or any(
                f["size"].lower() != "eco" or f["type"] != "web" or f["quantity"] != 1
                for f in formation.json()
            ):
                raise ValueError("Expected exactly one Eco web dyno")
            addons = api.get(f"/apps/{item['id']}/addons")
            addons.raise_for_status()
            if addons.json():
                raise ValueError("Unexpected add-ons; no deployment performed")
        for service in ["login", "ontology", "chat"]:
            item = state["services"][service]
            base = {
                "COA_SERVICE": service,
                "DATA_SCHEMA": cfg.get("DATA_SCHEMA", "coa_puroseka"),
                "ARTIFACT_STORE": "database",
                "SEMANTIC_VERSION": "puroseka-1.1.0",
            }
            database = databases[service]
            if service == "ontology":
                base.update(
                    DATABASE_URL=database,
                    LOGIN_SERVICE_URL=state["services"]["login"]["url"],
                    LOGIN_SERVICE_TOKEN=cfg["LOGIN_SERVICE_TOKEN"],
                    ONTOLOGY_SERVICE_TOKEN=cfg["ONTOLOGY_SERVICE_TOKEN"],
                )
            else:
                base.update(
                    CONTROL_DATABASE_URL=database,
                    LOGIN_SERVICE_TOKEN=cfg["LOGIN_SERVICE_TOKEN"],
                )
                if service == "chat":
                    base.update(
                        LOGIN_SERVICE_URL=state["services"]["login"]["url"],
                        ONTOLOGY_SERVICE_URL=state["services"]["ontology"]["url"],
                        ONTOLOGY_SERVICE_TOKEN=cfg["ONTOLOGY_SERVICE_TOKEN"],
                        MISTRAL_API_KEY=cfg["MISTRAL_API_KEY"],
                        MISTRAL_MODEL="ministral-8b-latest",
                        PUBLIC_ORIGIN=cfg["PUBLIC_ORIGIN"],
                    )
            path = "/apps/" + item["id"]
            api.patch(path + "/config-vars", json=base).raise_for_status()
            api.patch(path, json={"build_stack": manifest["stack"]}).raise_for_status()
            api.put(
                path + "/buildpack-installations",
                json={
                    "updates": [{"buildpack": b["url"]} for b in manifest["buildpacks"]]
                },
            ).raise_for_status()
            archive = upload(api, item["id"], service, revision)
            response = api.post(
                path + "/builds",
                json={"source_blob": {"url": archive, "version": revision}},
            )
            response.raise_for_status()
            build = response.json()
            item["build_id"] = build["id"]
            save(state)
            print(
                json.dumps(
                    {"service": service, "build": "pending", "stack": manifest["stack"]}
                ),
                flush=True,
            )
            for _ in range(180):
                response = api.get(path + "/builds/" + build["id"])
                response.raise_for_status()
                build = response.json()
                if build["status"] == "succeeded":
                    break
                if build["status"] == "failed":
                    raise RuntimeError(
                        "Build failed for " + service + "; inspect saved build_id"
                    )
                time.sleep(3)
            else:
                raise RuntimeError("Build pending; inspect saved build_id")
            item.update(
                revision=revision,
                stack=manifest["stack"],
                status="deployed",
                formation=[{"type": "web", "size": "Eco", "quantity": 1}],
            )
            save(state)
            print(
                json.dumps(
                    {
                        "service": service,
                        "status": "deployed",
                        "size": "Eco",
                        "addons": 0,
                    }
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
