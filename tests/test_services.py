"""Service boundaries independent of AWS or live model quotas."""

import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from shared.config import settings
from services.ontology.query import validate_sql, QueryRejected
from services.chat import auth
from services.login.main import app as login
from services.ontology.main import app as ontology
from services.chat.main import app as chat
from scripts.deploy_heroku import validate_resources


def test_eco_only_manifest():
    manifest = json.loads(
        (Path(__file__).resolve().parents[1] / "app.json").read_text()
    )
    validate_resources(manifest)
    for forbidden in ["basic", "standard-1x", "performance-m"]:
        with pytest.raises(ValueError):
            validate_resources(
                {**manifest, "formation": {"web": {"quantity": 1, "size": forbidden}}}
            )
    with pytest.raises(ValueError):
        validate_resources({**manifest, "addons": ["heroku-postgresql"]})
    with pytest.raises(ValueError):
        validate_resources({**manifest, "scripts": {"postdeploy": "echo run"}})


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM coa_puroseka.auth_accounts",
        "SELECT * FROM coa_puroseka.auth_sessions",
        "SELECT * FROM coa_puroseka.chat_jobs",
        "SELECT * FROM public.events",
        "WITH auth_accounts AS (SELECT 1) SELECT * FROM coa_puroseka.auth_accounts",
        "SELECT * FROM data.events",
    ],
)
def test_private_tables_and_foreign_schemas_rejected(query):
    with pytest.raises(QueryRejected):
        validate_sql(query, {"events"})


def test_service_endpoints_require_credentials(monkeypatch):
    monkeypatch.setitem(settings(), "LOGIN_SERVICE_TOKEN", "test-login-token")
    monkeypatch.setitem(settings(), "ONTOLOGY_SERVICE_TOKEN", "test-ontology-token")
    assert (
        TestClient(login)
        .post("/internal/introspect", json={"token": "arbitrary"})
        .status_code
        == 401
    )
    assert (
        TestClient(ontology)
        .post("/internal/query", json={"sql": "SELECT 1"})
        .status_code
        == 401
    )
    assert (
        TestClient(ontology)
        .get("/api/catalog", headers={"Authorization": "Bearer test-ontology-token"})
        .status_code
        == 401
    )
    assert TestClient(chat).get("/api/schema").status_code == 401
    assert (
        TestClient(chat).post("/api/chat/jobs", json={"question": "hello"}).status_code
        == 401
    )


def test_browser_csrf_and_no_session_disclosure(monkeypatch):
    monkeypatch.setitem(settings(), "PUBLIC_ORIGIN", "https://chat.example.test")

    def rpc(service, method, path, **kwargs):
        if path == "/internal/login":
            return {
                "token": "private-session",
                "user": {
                    "username": "demo@example.test",
                    "role": "admin",
                    "csrf": "csrf-value",
                },
            }
        return {"username": "demo@example.test", "role": "admin", "csrf": "csrf-value"}

    monkeypatch.setattr(auth, "call", rpc)
    client = TestClient(chat, base_url="https://chat.example.test")
    assert (
        client.post(
            "/api/auth/login",
            json={"username": "demo@example.test", "password": "irrelevant"},
            headers={"Origin": "https://foreign.test"},
        ).status_code
        == 403
    )
    r = client.post(
        "/api/auth/login",
        json={"username": "demo@example.test", "password": "irrelevant"},
        headers={"Origin": "https://chat.example.test"},
    )
    assert r.status_code == 200 and "private-session" not in r.text
    assert "HttpOnly" in r.headers["set-cookie"] and "Secure" in r.headers["set-cookie"]
    assert "_session_token" not in client.get("/api/auth/me").json()
    r = client.put(
        "/api/admin/token-policy",
        json={"enabled": False, "token_limit": 100000, "window_seconds": 3600},
        headers={"Origin": "https://chat.example.test"},
    )
    assert r.status_code == 403


def test_nonadmin_cannot_review(monkeypatch):
    monkeypatch.setattr(
        auth,
        "call",
        lambda *a, **k: {"username": "user@example.test", "role": "user", "csrf": "x"},
    )
    client = TestClient(chat, cookies={"coa_session": "session"})
    assert (
        client.get("/api/jobs/00000000-0000-0000-0000-000000000000/review").status_code
        == 403
    )


def test_decoupled_browser_cors_cookie_and_csrf(monkeypatch):
    # Rebuild the real ASGI middleware with the deployment's frontend origin.
    from starlette.middleware.cors import CORSMiddleware

    origin = "https://chat.coa.puroseka.02labs.me"
    monkeypatch.setitem(settings(), "PUBLIC_ORIGIN", origin)
    middleware = next(m for m in chat.user_middleware if m.cls is CORSMiddleware)
    monkeypatch.setitem(middleware.kwargs, "allow_origins", [origin])
    monkeypatch.setattr(chat, "middleware_stack", None)
    user = {"username": "demo@example.test", "role": "admin", "csrf": "csrf-value"}
    monkeypatch.setattr(
        auth,
        "call",
        lambda *a, **k: (
            {"token": "private-session", "user": user}
            if a[2] == "/internal/login"
            else user
        ),
    )
    monkeypatch.setattr(
        "services.chat.main.control.update_limit", lambda *a: {"enabled": False}
    )
    client = TestClient(chat, base_url="https://api.coa.puroseka.02labs.me")
    preflight = {
        "Origin": origin,
        "Access-Control-Request-Method": "PUT",
        "Access-Control-Request-Headers": "Content-Type,X-CSRF-Token",
    }
    response = client.options("/api/admin/token-policy", headers=preflight)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"
    assert (
        client.options(
            "/api/admin/token-policy",
            headers={**preflight, "Origin": "https://foreign.test"},
        ).status_code
        == 400
    )
    response = client.post(
        "/api/auth/login",
        headers={"Origin": origin},
        json={"username": user["username"], "password": "irrelevant"},
    )
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert "domain=" not in cookie.lower()
    assert client.get("/api/auth/me", headers={"Origin": origin}).json() == user
    policy = {"enabled": False, "token_limit": 100000, "window_seconds": 3600}
    assert (
        client.put(
            "/api/admin/token-policy", headers={"Origin": origin}, json=policy
        ).status_code
        == 403
    )
    assert (
        client.put(
            "/api/admin/token-policy",
            headers={"Origin": "https://foreign.test", "X-CSRF-Token": "csrf-value"},
            json=policy,
        ).status_code
        == 403
    )
    assert (
        client.put(
            "/api/admin/token-policy",
            headers={"Origin": origin, "X-CSRF-Token": "csrf-value"},
            json=policy,
        ).status_code
        == 200
    )
    assert client.get("/").status_code == 404
