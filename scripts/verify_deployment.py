"""Verify the deployed Eco services without publishing credentials or source rows.

--live-model runs a small fixed acceptance set through the actual Mistral sandbox.
--restart restarts existing Eco web processes and checks durable sessions/usage.
No apps, add-ons, databases, or additional dynos are provisioned by this check.
"""

import argparse
import json
import netrc
from pathlib import Path
import time
import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def verify(live_model=False, restart=False):
    cfg = dotenv_values(ROOT / ".env")
    state = json.loads((ROOT / ".deploy/heroku-services.json").read_text())
    checks = []
    model_results = []
    base = state["services"]["chat"]["url"]

    def check(ok, label):
        if not ok:
            raise RuntimeError("Check failed: " + label)
        checks.append(label)
        print("Passed: " + label, flush=True)

    def wait_ready(url):
        for _ in range(30):
            try:
                if httpx.get(url + "/api/health", timeout=25).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(2)
        raise RuntimeError("Service readiness did not pass")

    token = netrc.netrc().authenticators("api.heroku.com")[2]
    with httpx.Client(
        base_url="https://api.heroku.com",
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.heroku+json; version=3",
        },
        timeout=25,
    ) as platform:
        for service, item in state["services"].items():
            f = platform.get("/apps/" + item["id"] + "/formation")
            f.raise_for_status()
            check(
                len(f.json()) == 1
                and f.json()[0]["type"] == "web"
                and f.json()[0]["size"].lower() == "eco"
                and f.json()[0]["quantity"] == 1,
                service + " uses one Eco web dyno",
            )
            addons = platform.get("/apps/" + item["id"] + "/addons")
            addons.raise_for_status()
            check(not addons.json(), service + " has no add-ons")
            values = platform.get("/apps/" + item["id"] + "/config-vars")
            values.raise_for_status()
            values = values.json()
            check(
                bool(values.get("MISTRAL_API_KEY")) == (service == "chat"),
                service + " has only its required model configuration",
            )
            check(
                not any(
                    values.get(k)
                    for k in (
                        "MIGRATION_ADMIN_DATABASE_URL",
                        "ADMIN_DATABASE_URL",
                        "ADMIN_PASSWORD",
                        "CHAT_PASSWORD",
                        "AWS_ACCESS_KEY_ID",
                        "AWS_SECRET_ACCESS_KEY",
                        "AWS_SESSION_TOKEN",
                    )
                ),
                service + " has no operator credentials",
            )
            wait_ready(item["url"])
            check(True, service + " database readiness")
        with httpx.Client(base_url=base, timeout=27) as browser:
            check(
                browser.get("/").status_code == 200
                and browser.get("/admin/sources").status_code == 200,
                "Chat and administrator pages load",
            )
            check(
                browser.get("/api/schema").status_code == 401,
                "Anonymous source access denied",
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
            response = browser.post(
                "/api/auth/login", json=credentials, headers={"Origin": base}
            )
            check(
                response.status_code == 200 and response.json().get("role") == "admin",
                "Configured shared administrator signs in",
            )
            cookie = response.headers.get("set-cookie", "")
            check(
                "Secure" in cookie
                and "HttpOnly" in cookie
                and "SameSite=strict" in cookie,
                "Secure browser session cookie",
            )
            csrf = response.json()["csrf"]
            headers = {"Origin": base, "X-CSRF-Token": csrf}
            session = browser.cookies.get("coa_session")
            check(
                "_session_token" not in browser.get("/api/auth/me").text,
                "Session response excludes internal bearer token",
            )
            schema = browser.get("/api/schema")
            check(
                schema.status_code == 200 and len(schema.json()) == 16,
                "Exactly 16 source tables exposed",
            )
            check(
                not any(
                    n.startswith(("auth_", "chat_", "ontology_")) for n in schema.json()
                ),
                "Private application tables absent from model schema",
            )
            check(
                browser.get("/api/tables/events").status_code == 200,
                "Source preview through gateway",
            )
            check(
                browser.get("/api/tables/auth_sessions").status_code == 404,
                "Private-table preview denied",
            )
            check(
                browser.get("/api/ontology.ttl").status_code == 200,
                "Persisted RDF export",
            )
            manifest = browser.get("/api/ranking-manifest").json()
            check(
                manifest["row_count"] == 1117619
                and manifest["source_directory_count"] == 180,
                "Complete ranking coverage retained",
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
                "CSRF required on policy changes",
            )
            usage = browser.get("/api/usage").json()
            check(usage["enabled"] is False, "Token enforcement remains off")
            internal_headers = {
                "Authorization": "Bearer " + cfg["ONTOLOGY_SERVICE_TOKEN"],
                "X-COA-Session": session,
            }
            ontology = state["services"]["ontology"]["url"]
            check(
                httpx.post(
                    ontology + "/internal/query", json={"sql": "SELECT 1"}, timeout=25
                ).status_code
                == 401,
                "Direct tool request needs service authentication",
            )
            r = httpx.post(
                ontology + "/internal/query",
                headers=internal_headers,
                json={"sql": "SELECT * FROM coa_puroseka.auth_accounts"},
                timeout=25,
            )
            check(r.status_code == 400, "SQL guard denies authentication tables")
            r = httpx.post(
                ontology + "/internal/query",
                headers=internal_headers,
                json={"sql": "SELECT count(*) AS n FROM coa_puroseka.events"},
                timeout=25,
            )
            check(
                r.status_code == 200 and r.json()["rows"] == [{"n": 180}],
                "Live SQL sees all 180 events",
            )
            if live_model:
                examples = json.loads((ROOT / "semantic/few_shots.json").read_text())
                cases = [
                    (
                        "base",
                        "How many events are in the dataset? Return the total event count.",
                    ),
                    ("enriched", examples[0]["question"]),
                    ("enriched", examples[1]["question"]),
                ]
                for mode, question in cases:
                    r = browser.post(
                        "/api/chat/jobs",
                        json={"question": question, "mode": mode, "history": []},
                        headers=headers,
                    )
                    check(
                        r.status_code == 202, mode + " response accepted asynchronously"
                    )
                    job = r.json()["id"]
                    for _ in range(240):
                        current = browser.get("/api/jobs/" + job)
                        current.raise_for_status()
                        current = current.json()
                        if current["state"] != "running":
                            break
                        time.sleep(1)
                    check(
                        current["state"] == "done"
                        and current["result"]["status"] == "ok",
                        mode + " model response completed",
                    )
                    review = browser.get("/api/jobs/" + job + "/review")
                    review.raise_for_status()
                    data = review.json()
                    queries = [
                        call
                        for call in data["calls"]
                        if call["name"] == "execute_query" and call["status"] == "ok"
                    ]
                    check(
                        bool(queries) and bool(queries[-1]["output"]["rows"]),
                        mode + " response has executed SQL evidence",
                    )
                    check(
                        all(
                            secret not in review.text
                            for secret in (
                                cfg["MISTRAL_API_KEY"],
                                cfg["LOGIN_SERVICE_TOKEN"],
                                cfg["ONTOLOGY_SERVICE_TOKEN"],
                                session,
                            )
                        ),
                        mode + " review excludes credentials",
                    )
                    if mode == "base":
                        check(
                            list(queries[-1]["output"]["rows"][0].values()) == [180],
                            "Base mode returns the correct event count",
                        )
                    model_results.append(
                        {
                            "mode": mode,
                            "question": question,
                            "job_id": job,
                            "row_count": len(queries[-1]["output"]["rows"]),
                            "status": "ok",
                        }
                    )
                    if "daily score evolution" in question:
                        rows = queries[-1]["output"]["rows"]
                        check(
                            "score_change_since_previous_observed_day" in rows[0]
                            and rows[0]["score_change_since_previous_observed_day"]
                            is None
                            and "elapsed_calendar_days" in rows[0],
                            "Daily evolution reports changes and missing-day gaps",
                        )
            if restart:
                before = browser.get("/api/usage").json()
                for service in ("login", "chat"):
                    item = state["services"][service]
                    platform.delete("/apps/" + item["id"] + "/dynos").raise_for_status()
                    wait_ready(item["url"])
                check(
                    browser.get("/api/auth/me").status_code == 200,
                    "Session survives Eco process restarts",
                )
                after = browser.get("/api/usage").json()
                check(
                    after["used_tokens"] == before["used_tokens"]
                    or after["resets_at"] != before["resets_at"],
                    "Metering persists across Eco restarts",
                )
            check(
                browser.post("/api/auth/logout", headers=headers).status_code == 200
                and browser.get("/api/auth/me").status_code == 401,
                "Logout revokes session",
            )
    report = {
        "status": "passed",
        "chat_url": base,
        "checks": checks,
        "live_model": live_model,
        "model_results": model_results,
        "eco_only": True,
        "database_imported": True,
        "application_ready": True,
    }
    f = ROOT / "artifacts/heroku-deployment-verification.json"
    f.write_text(json.dumps(report, indent=2) + "\n")
    f.chmod(0o600)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-model", action="store_true")
    parser.add_argument("--restart", action="store_true")
    args = parser.parse_args()
    try:
        verify(args.live_model, args.restart)
    except Exception as exc:
        print(
            "Deployment verification failed:",
            type(exc).__name__,
            str(exc) if isinstance(exc, RuntimeError) else "See private service logs.",
        )
        raise SystemExit(1)
