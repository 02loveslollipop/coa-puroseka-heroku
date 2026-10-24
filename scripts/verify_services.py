"""Run all three services locally against a migrated scratch database.

Uses the existing private administrator credentials. --live-model adds two
bounded end-to-end requests (Base and Enhanced) using the configured Mistral key.
No cloud resources are created by this check.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from dotenv import dotenv_values
import psycopg

ROOT = Path(__file__).resolve().parents[1]


def verify(database_url, live=False):
    cfg = dotenv_values(ROOT / ".env")
    base_env = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "DATA_SCHEMA": "coa_puroseka",
        "DATABASE_URL": database_url,
        "CONTROL_DATABASE_URL": database_url,
        "ARTIFACT_STORE": "database",
        "LOGIN_SERVICE_URL": "http://127.0.0.1:8101",
        "ONTOLOGY_SERVICE_URL": "http://127.0.0.1:8102",
        "PUBLIC_ORIGIN": "http://127.0.0.1:8100",
        "LOGIN_SERVICE_TOKEN": "local-test-login-service-only",
        "ONTOLOGY_SERVICE_TOKEN": "local-test-ontology-service-only",
    }
    ports = {"chat": 8100, "login": 8101, "ontology": 8102}
    procs = {}
    logs = {}
    checks = []

    def start(service):
        log = ROOT / "artifacts" / ("local-" + service + ".log")
        logs[service] = log.open("a")
        log.chmod(0o600)
        env = {**base_env, "COA_SERVICE": service}
        if service == "chat" and live:
            env["MISTRAL_API_KEY"] = cfg["MISTRAL_API_KEY"]
        else:
            env["MISTRAL_API_KEY"] = ""
        procs[service] = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "services." + service + ".main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(ports[service]),
            ],
            cwd=ROOT,
            env=env,
            stdout=logs[service],
            stderr=subprocess.STDOUT,
        )

    def ready(service):
        for _ in range(60):
            try:
                if (
                    httpx.get(
                        "http://127.0.0.1:" + str(ports[service]) + "/api/health",
                        timeout=2,
                    ).status_code
                    == 200
                ):
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.3)
        raise RuntimeError("Local service readiness failed: " + service)

    def check(condition, label):
        if not condition:
            raise RuntimeError("Check failed: " + label)
        checks.append(label)
        print("Passed:", label, flush=True)

    try:
        for service in ["login", "ontology", "chat"]:
            start(service)
            ready(service)
        with httpx.Client(base_url=base_env["PUBLIC_ORIGIN"], timeout=25) as browser:
            check(browser.get("/").status_code == 200, "Cloudscape SPA served")
            check(
                browser.get("/api/schema").status_code == 401,
                "Anonymous schema access denied",
            )
            check(
                httpx.post(
                    "http://127.0.0.1:8102/internal/query", json={"sql": "SELECT 1"}
                ).status_code
                == 401,
                "Direct tool access denied",
            )
            credentials = {
                "username": cfg["ADMIN_USERNAME"],
                "password": cfg["ADMIN_PASSWORD"],
            }
            check(
                browser.post(
                    "/api/auth/login",
                    json=credentials,
                    headers={"Origin": "https://foreign.invalid"},
                ).status_code
                == 403,
                "Cross-origin login denied",
            )
            r = browser.post(
                "/api/auth/login",
                json=credentials,
                headers={"Origin": base_env["PUBLIC_ORIGIN"]},
            )
            check(
                r.status_code == 200
                and r.json()["role"] == "admin"
                and "_session_token" not in r.text,
                "Shared administrator sign-in",
            )
            csrf = r.json()["csrf"]
            headers = {"Origin": base_env["PUBLIC_ORIGIN"], "X-CSRF-Token": csrf}
            schema = browser.get("/api/schema").json()
            check(
                len(schema) == 16
                and not any(
                    x.startswith(("auth_", "chat_", "ontology_")) for x in schema
                ),
                "Only source tables exposed to model",
            )
            check(
                browser.get("/api/tables/events").status_code == 200,
                "Source preview through ontology service",
            )
            check(
                browser.get("/api/tables/auth_sessions").status_code == 404,
                "Private-table preview denied",
            )
            check(
                browser.get("/api/ontology.ttl").status_code == 200,
                "RDF export from persistent database",
            )
            check(
                browser.get(
                    "/api/rankings", params={"event_id": 1, "rank": 100}
                ).status_code
                == 200,
                "Ranking timeline API",
            )
            check(
                browser.put(
                    "/api/admin/token-policy",
                    json={
                        "enabled": False,
                        "token_limit": 100000,
                        "window_seconds": 3600,
                    },
                ).status_code
                == 403,
                "CSRF required for policy changes",
            )
            usage = browser.get("/api/usage").json()
            check(usage["enabled"] is False, "Token enforcement remains off")
            for service in ["chat", "login"]:
                procs[service].terminate()
                procs[service].wait(timeout=15)
                logs[service].close()
                start(service)
                ready(service)
            check(
                browser.get("/api/auth/me").status_code == 200,
                "Session survives chat and login restarts",
            )
            check(
                browser.get("/api/usage").json()["used_tokens"] == usage["used_tokens"],
                "Metering survives restarts",
            )
            token = browser.cookies.get("coa_session")
            internal = {
                "Authorization": "Bearer " + base_env["ONTOLOGY_SERVICE_TOKEN"],
                "X-COA-Session": token,
            }
            r = httpx.post(
                "http://127.0.0.1:8102/internal/query",
                headers=internal,
                json={"sql": "SELECT password_hash FROM coa_puroseka.auth_accounts"},
                timeout=25,
            )
            check(r.status_code == 400, "Guarded SQL blocks authentication data")
            r = httpx.post(
                "http://127.0.0.1:8102/internal/query",
                headers=internal,
                json={"sql": "SELECT count(*) AS n FROM coa_puroseka.events"},
                timeout=25,
            )
            check(
                r.status_code == 200 and r.json()["rows"] == [{"n": 180}],
                "Guarded SQL returns all 180 events",
            )
            # Persist an interrupted job; restart must expire it without a model replay.
            job = str(uuid4())
            with psycopg.connect(database_url) as conn:
                conn.execute(
                    "INSERT INTO coa_puroseka.chat_jobs(id,username,request) VALUES(%s,%s,'{}'::jsonb)",
                    (job, cfg["ADMIN_USERNAME"]),
                )
            procs["chat"].terminate()
            procs["chat"].wait(timeout=15)
            logs["chat"].close()
            start("chat")
            ready("chat")
            check(
                browser.get("/api/jobs/" + job).json()["state"] == "expired",
                "Interrupted jobs expire without replay",
            )
            if live:
                for mode in ["base", "enriched"]:
                    r = browser.post(
                        "/api/chat/jobs",
                        json={
                            "question": "How many events are in the dataset? Return the total event count.",
                            "mode": mode,
                            "history": [],
                        },
                        headers=headers,
                    )
                    check(r.status_code == 202, mode + " job accepted asynchronously")
                    job_id = r.json()["id"]
                    for _ in range(300):
                        state = browser.get("/api/jobs/" + job_id).json()
                        if state["state"] != "running":
                            break
                        time.sleep(1)
                    check(
                        state["state"] == "done" and state["result"]["status"] == "ok",
                        mode + " live model response grounded in SQL",
                    )
                    review = browser.get("/api/jobs/" + job_id + "/review")
                    check(
                        review.status_code == 200
                        and any(
                            c["name"] == "execute_query" for c in review.json()["calls"]
                        ),
                        mode + " tool review captured",
                    )
                    check(
                        cfg["MISTRAL_API_KEY"] not in review.text
                        and token not in review.text,
                        mode + " review excludes secrets and session tokens",
                    )
            check(
                browser.post("/api/auth/logout", headers=headers).status_code == 200
                and browser.get("/api/auth/me").status_code == 401,
                "Logout revokes persistent session",
            )
        report = {
            "status": "passed",
            "checks": checks,
            "live_model": live,
            "cloud_resources_created": 0,
        }
        p = ROOT / "artifacts/service-verification.json"
        p.write_text(json.dumps(report, indent=2) + "\n")
        p.chmod(0o600)
        return report
    finally:
        for p in procs.values():
            if p.poll() is None:
                p.terminate()
        for p in procs.values():
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
        for log in logs.values():
            log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-model", action="store_true")
    args = parser.parse_args()
    url = os.environ.get("LOCAL_TEST_DATABASE_URL", "")
    from urllib.parse import urlsplit

    if urlsplit(url).hostname not in {"127.0.0.1", "localhost"}:
        raise SystemExit(
            "LOCAL_TEST_DATABASE_URL must point to a disposable local database"
        )
    try:
        verify(url, args.live_model)
    except Exception as exc:
        print(
            "Service verification failed:",
            type(exc).__name__,
            str(exc),
            file=sys.stderr,
        )
        raise SystemExit(1)
