"""Bounded service calls; credentials never enter inference/review payloads."""

from contextvars import ContextVar
import httpx
from fastapi import HTTPException
from shared.config import settings
from shared.query_errors import QueryError, QueryRejected

session_token = ContextVar("service_session", default="")


def call(service, method, path, *, body=None, token=None, raw=False):
    cfg = settings()
    base = cfg[f"{service.upper()}_SERVICE_URL"].rstrip("/")
    secret = cfg[f"{service.upper()}_SERVICE_TOKEN"]
    try:
        with httpx.Client(
            timeout=httpx.Timeout(22, connect=8), follow_redirects=False
        ) as client:
            response = client.request(
                method,
                base + path,
                headers={
                    "Authorization": "Bearer " + secret,
                    "X-COA-Session": token
                    if token is not None
                    else session_token.get(),
                },
                json=body,
            )
    except httpx.HTTPError:
        raise HTTPException(
            503,
            f"The {service} service is waking up or unavailable. Try again.",
            headers={"Retry-After": "5"},
        ) from None
    if not response.is_success:
        detail = "Service request failed."
        try:
            detail = response.json().get("detail", detail)
        except ValueError:
            pass
        if (
            service == "ontology"
            and path == "/internal/query"
            and isinstance(detail, dict)
        ):
            message = detail.get("message")
            if isinstance(message, str) and len(message) <= 500:
                if (
                    response.status_code == 422
                    and detail.get("code") == "invalid_query"
                ):
                    raise QueryError(message)
                if (
                    response.status_code == 400
                    and detail.get("code") == "query_rejected"
                ):
                    raise QueryRejected(message)
        raise HTTPException(
            response.status_code if response.status_code < 500 else 503,
            detail,
            headers={"Retry-After": "5"} if response.status_code >= 500 else None,
        )
    if raw:
        return response.content, response.headers.get(
            "content-type", "application/json"
        ).split(";")[0]
    return response.json()
