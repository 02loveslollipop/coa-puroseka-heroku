"""Local password sign-in with opaque, revocable, server-side browser sessions."""

import hashlib
import hmac
import secrets
from datetime import timedelta

from fastapi import HTTPException, Request

from app.config import settings
from app.control import control_connection, utcnow
from app.database import namespace

COOKIE = "coa_session"
SESSION_SECONDS = 3600


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1)
    return salt + ":" + digest.hex()


def password_matches(password, encoded):
    try:
        salt, _ = encoded.split(":", 1)
        return hmac.compare_digest(password_hash(password, salt), encoded)
    except (ValueError, AttributeError):
        return False


def fingerprint(value):
    return hashlib.sha256(value.encode()).hexdigest()


def check_origin(request: Request):
    expected = settings().get("PUBLIC_ORIGIN", str(request.base_url).rstrip("/"))
    if request.headers.get("origin") != expected:
        raise HTTPException(403, "This request must originate from this application.")


def login(username, password, ip):
    username = username.strip().lower()
    attempt_key = fingerprint(ip)
    # Durable attempt limit also covers nonexistent accounts and service restarts.
    with control_connection() as conn:
        row = conn.execute(
            f"INSERT INTO {namespace()}.auth_login_attempts(key,attempts,resets_at) VALUES (%s,1,%s) "
            "ON CONFLICT (key) DO UPDATE SET "
            f"attempts=CASE WHEN {namespace()}.auth_login_attempts.resets_at < now() THEN 1 ELSE {namespace()}.auth_login_attempts.attempts+1 END,"
            f"resets_at=CASE WHEN {namespace()}.auth_login_attempts.resets_at < now() THEN EXCLUDED.resets_at ELSE {namespace()}.auth_login_attempts.resets_at END "
            "RETURNING attempts",
            (attempt_key, utcnow() + timedelta(minutes=15)),
        ).fetchone()
    if row["attempts"] > 12:
        raise HTTPException(
            429,
            "Too many sign-in attempts. Try again in 15 minutes.",
            headers={"Retry-After": "900"},
        )
    with control_connection() as conn:
        user = conn.execute(
            f"SELECT * FROM {namespace()}.auth_accounts WHERE username=%s", (username,)
        ).fetchone()
    encoded = (
        user["password_hash"] if user else password_hash(secrets.token_urlsafe(32))
    )
    valid = password_matches(password, encoded)
    if not valid or not user or not user["enabled"]:
        raise HTTPException(401, "The email or password is incorrect.")
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with control_connection() as conn:
        conn.execute(
            f"DELETE FROM {namespace()}.auth_sessions WHERE expires_at < now()"
        )
        conn.execute(
            f"INSERT INTO {namespace()}.auth_sessions(token_hash,username,csrf,expires_at) VALUES (%s,%s,%s,%s)",
            (
                fingerprint(token),
                username,
                csrf,
                utcnow() + timedelta(seconds=SESSION_SECONDS),
            ),
        )
    return token, {"username": username, "role": user["role"], "csrf": csrf}


def identity(token):
    with control_connection() as conn:
        row = conn.execute(
            f"SELECT a.username,a.role,s.csrf FROM {namespace()}.auth_sessions s JOIN {namespace()}.auth_accounts a "
            "ON a.username=s.username WHERE s.token_hash=%s AND s.expires_at > now() AND a.enabled",
            (fingerprint(token),),
        ).fetchone()
    if not row:
        raise HTTPException(401, "Your session has expired. Sign in again.")
    return row


def revoke(token):
    with control_connection() as conn:
        conn.execute(
            f"DELETE FROM {namespace()}.auth_sessions WHERE token_hash=%s",
            (fingerprint(token),),
        )
