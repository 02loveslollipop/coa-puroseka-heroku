"""Browser session checks delegate identity to the login service."""

import hmac
from fastapi import HTTPException, Request
from app.config import settings
from shared.rpc import call

COOKIE = "coa_session"
SESSION_SECONDS = 3600


def public_user(user):
    return {k: user[k] for k in ("username", "role", "csrf")}


def check_origin(request: Request):
    expected = settings().get("PUBLIC_ORIGIN", str(request.base_url).rstrip("/"))
    if request.headers.get("origin") != expected:
        raise HTTPException(403, "This request must originate from this application.")


def login(username, password, ip):
    result = call(
        "login",
        "POST",
        "/internal/login",
        body={"username": username, "password": password, "client_ip": ip},
    )
    return result["token"], result["user"]


def current_user(request: Request):
    token = request.cookies.get(COOKIE, "")
    if not token:
        raise HTTPException(401, "Sign in to continue.")
    user = call("login", "POST", "/internal/introspect", body={"token": token})
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        check_origin(request)
        if not hmac.compare_digest(
            request.headers.get("x-csrf-token", ""), user["csrf"]
        ):
            raise HTTPException(
                403, "The session security token is missing or invalid."
            )
    return {**user, "_session_token": token}


def admin_user(request: Request):
    user = current_user(request)
    if user["role"] != "admin":
        raise HTTPException(
            403, "COA administration requires an administrator account."
        )
    return user


def logout(request: Request):
    call(
        "login",
        "POST",
        "/internal/logout",
        body={"token": request.cookies.get(COOKIE, "")},
    )
