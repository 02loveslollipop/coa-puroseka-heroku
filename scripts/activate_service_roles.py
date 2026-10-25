"""Switch only DB credentials on the three existing Eco apps; no code rebuild.

Keeps a private rollback receipt. Failed activation restores the previous DB
configuration; unrelated config (including models and token policy) is preserved.
"""

import json
import netrc
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import dotenv_values
import httpx
import psycopg
from scripts.provision_service_roles import service_database, role_name

ROOT = Path(__file__).resolve().parents[1]


def activate():
    cfg = dotenv_values(ROOT / ".env")
    state = json.loads((ROOT / ".deploy/heroku-services.json").read_text())
    if set(state["services"]) != {"login", "ontology", "chat"}:
        raise ValueError("Expected the three recorded existing services")
    databases = {name: service_database(cfg, name) for name in state["services"]}
    for name, url in databases.items():
        with psycopg.connect(url, connect_timeout=10) as conn:
            assert conn.pgconn.ssl_in_use
            assert conn.execute("SELECT current_user").fetchone()[0] == role_name(
                cfg.get("DATA_SCHEMA", "coa_puroseka"), name
            )
            if any(
                conn.execute(
                    "SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls,rolreplication "
                    "FROM pg_roles WHERE rolname=current_user"
                ).fetchone()
            ):
                raise ValueError("Runtime role has administrative privileges")
    token = (
        os.getenv("HEROKU_API_KEY") or netrc.netrc().authenticators("api.heroku.com")[2]
    )
    old, changed = {}, []
    with httpx.Client(
        base_url="https://api.heroku.com",
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.heroku+json; version=3",
        },
        timeout=30,
    ) as api:
        for name, item in state["services"].items():
            path = "/apps/" + item["id"]
            response = api.get(path + "/formation")
            response.raise_for_status()
            formation = response.json()
            if len(formation) != 1 or any(
                f["type"] != "web" or f["quantity"] != 1 or f["size"].lower() != "eco"
                for f in formation
            ):
                raise ValueError("Expected exactly one existing Eco web dyno")
            response = api.get(path + "/addons")
            response.raise_for_status()
            if response.json():
                raise ValueError("Unexpected add-ons")
            response = api.get(path + "/config-vars")
            response.raise_for_status()
            key = "DATABASE_URL" if name == "ontology" else "CONTROL_DATABASE_URL"
            old[name] = {key: response.json().get(key)}
        receipt = ROOT / ".deploy/service-role-rollback.json"
        descriptor = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(old, stream)
        try:
            for name in ("login", "ontology", "chat"):
                item = state["services"][name]
                key = "DATABASE_URL" if name == "ontology" else "CONTROL_DATABASE_URL"
                # Record before the request: a timeout may still have applied it.
                changed.append(name)
                api.patch(
                    "/apps/" + item["id"] + "/config-vars", json={key: databases[name]}
                ).raise_for_status()
                with httpx.Client(timeout=20) as public:
                    for _ in range(20):
                        try:
                            if (
                                public.get(
                                    item["url"].rstrip("/") + "/api/health"
                                ).status_code
                                == 200
                            ):
                                break
                        except httpx.HTTPError:
                            pass
                        time.sleep(2)
                    else:
                        raise RuntimeError("Service did not become healthy")
                print(
                    json.dumps(
                        {"service": name, "dedicated_role": True, "healthy": True}
                    ),
                    flush=True,
                )
        except Exception:
            for name in reversed(changed):
                api.patch(
                    "/apps/" + state["services"][name]["id"] + "/config-vars",
                    json=old[name],
                ).raise_for_status()
            raise


if __name__ == "__main__":
    try:
        activate()
    except Exception as exc:
        print("Role activation halted: " + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
