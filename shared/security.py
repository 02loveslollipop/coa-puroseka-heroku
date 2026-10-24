"""Service credentials and browser authorization are separate checks."""

import hmac
from fastapi import HTTPException, Request
from app.config import settings


def require_service(request: Request, key: str):
    expected = settings().get(key, "")
    actual = request.headers.get("authorization", "")
    if not expected or not hmac.compare_digest(actual, "Bearer " + expected):
        raise HTTPException(401, "Service authentication required.")


async def headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    return response
