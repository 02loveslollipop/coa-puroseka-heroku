import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, HTTPException, Depends, Request, Response, BackgroundTasks
from fastapi.encoders import jsonable_encoder
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from psycopg import Error as DatabaseError
from app.config import settings
from app.control import control_connection
from app.inference import infer
from app import auth, control, tracing
from app.metering import principal
from shared.rpc import session_token, call
from shared.security import database_unavailable


@asynccontextmanager
async def lifespan(app):
    try:
        await asyncio.to_thread(control.interrupt_jobs)
    except Exception:
        logging.getLogger(__name__).warning(
            "Database is not ready; chat jobs cannot start yet."
        )
    yield


app = FastAPI(title="Puroseka Semantic Context PoC", version="1.0.0", lifespan=lifespan)
app.add_exception_handler(DatabaseError, database_unavailable)
_inflight = asyncio.Semaphore(2)


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=10000)


class InferenceRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    mode: Literal["base", "enriched"] = "enriched"
    history: list[Message] = Field(default_factory=list, max_length=6)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    mode: Literal["base", "enriched"] = "enriched"
    history: list[Message] = Field(default_factory=list, max_length=6)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=1024)


class LimitRequest(BaseModel):
    enabled: bool
    token_limit: int = Field(ge=1, le=1000000000)
    window_seconds: int = Field(ge=60, le=2592000)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/auth/login")
def sign_in(body: LoginRequest, request: Request, response: Response):
    auth.check_origin(request)
    # Heroku appends the connecting address to X-Forwarded-For.
    ip = (
        request.headers.get("x-forwarded-for", request.client.host)
        .split(",")[-1]
        .strip()
    )
    token, user = auth.login(body.username, body.password, ip)
    response.set_cookie(
        auth.COOKIE,
        token,
        max_age=auth.SESSION_SECONDS,
        httponly=True,
        secure=settings().get("PUBLIC_ORIGIN", "").startswith("https://"),
        samesite="strict",
        path="/",
    )
    return auth.public_user(user)


@app.get("/api/auth/me")
def me(user=Depends(auth.current_user)):
    return auth.public_user(user)


@app.post("/api/auth/logout")
def sign_out(request: Request, response: Response, user=Depends(auth.current_user)):
    auth.logout(request)
    response.delete_cookie(auth.COOKIE, path="/")
    return {"status": "ok"}


@app.get("/api/usage")
def token_usage(user=Depends(auth.current_user)):
    return control.usage(user["username"])


@app.put("/api/admin/token-policy")
def token_policy(body: LimitRequest, user=Depends(auth.admin_user)):
    return control.update_limit(
        user["username"], body.enabled, body.token_limit, body.window_seconds
    )


async def run_job(job, user, body, audience):
    session = session_token.set(user.get("_session_token", ""))
    token = principal.set(user["username"])
    trace = []
    trace_token = tracing.calls.set(trace)

    async def save_progress(activity):
        await asyncio.to_thread(control.update_job_activity, job, activity)

    progress_token = tracing.progress.set(save_progress)
    try:
        async with _inflight:
            result = await infer(
                body["question"],
                body.get("mode", "enriched"),
                body.get("history", []),
                presentation=audience,
            )
        review = {
            "input": body,
            "output": {
                k: v
                for k, v in result.items()
                if k not in {"context", "query_messages", "answer_messages"}
            },
            "calls": trace,
        }
        if audience == "chat":
            result = {
                k: result[k]
                for k in (
                    "question",
                    "answer",
                    "status",
                    "usage",
                    "retry_after",
                    "mode",
                    "latency_ms",
                )
                if k in result
            }
        result["_review"] = review
        result["_activity"] = tracing.activity(trace)
        await asyncio.to_thread(control.finish_job, job, jsonable_encoder(result))
    except Exception:
        await asyncio.to_thread(
            control.finish_job,
            job,
            {
                "status": "error",
                "answer": "The response was interrupted. Try again.",
                "_activity": tracing.activity(trace),
                "_review": {
                    "input": body,
                    "output": {
                        "status": "error",
                        "answer": "The response was interrupted. Try again.",
                    },
                    "calls": trace,
                },
            },
        )
    finally:
        principal.reset(token)
        session_token.reset(session)
        tracing.calls.reset(trace_token)
        tracing.progress.reset(progress_token)


def submit_job(body, user, tasks, audience):
    budget = control.usage(user["username"])
    if budget["enabled"] and budget["remaining_tokens"] == 0:
        raise HTTPException(
            429,
            "Your token allowance is exhausted.",
            headers={
                "Retry-After": str(
                    max(
                        1, int((budget["resets_at"] - control.utcnow()).total_seconds())
                    )
                )
            },
        )
    try:
        job = control.create_job(user["username"], body)
    except ValueError as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": "5"}) from exc
    tasks.add_task(run_job, job, user, body, audience)
    return {"id": job, "state": "running"}


@app.post("/api/chat/jobs", status_code=202)
def chat_job(
    body: ChatRequest, tasks: BackgroundTasks, user=Depends(auth.current_user)
):
    return submit_job(body.model_dump(), user, tasks, "chat")


@app.post("/api/inference/jobs", status_code=202)
def inference_job(
    body: InferenceRequest, tasks: BackgroundTasks, user=Depends(auth.admin_user)
):
    return submit_job(body.model_dump(), user, tasks, "admin")


@app.get("/api/jobs/{job}")
def job_result(job: str, user=Depends(auth.current_user)):
    from uuid import UUID

    try:
        UUID(job)
    except ValueError:
        raise HTTPException(404, "Response not found.")
    result = control.get_job(user["username"], job)
    if not result:
        raise HTTPException(404, "Response not found.")
    payload = result.get("result") or {}
    review = payload.pop("_review", None)
    result["activity"] = payload.pop("_activity", None) or tracing.activity(
        (review or {}).get("calls", [])
    )
    if result["state"] == "running":
        # No partial response or private call data is exposed while a job runs.
        result["result"] = None
    elif payload:
        payload["job_id"] = str(result["id"])
        payload["activity"] = result["activity"]
    if user["role"] != "admin" and result.get("result"):
        result["result"] = {
            k: result["result"][k]
            for k in (
                "question",
                "answer",
                "status",
                "usage",
                "retry_after",
                "mode",
                "latency_ms",
                "job_id",
                "activity",
            )
            if k in result["result"]
        }
    return result


@app.get("/api/jobs/{job}/review")
def review_result(job: str, user=Depends(auth.admin_user)):
    from uuid import UUID

    try:
        UUID(job)
    except ValueError:
        raise HTTPException(404, "Response not found.")
    result = control.get_job(user["username"], job)
    if not result:
        raise HTTPException(404, "Response not found.")
    if result["state"] != "done":
        raise HTTPException(
            409, "Review data is available after the response completes."
        )
    review = (result.get("result") or {}).get("_review")
    if not review:
        raise HTTPException(
            404,
            "This earlier response has no review data. Regenerate it to capture the calls.",
        )
    return {"job_id": str(result["id"]), **review}


@app.get("/api/health")
def health():
    try:
        with control_connection() as conn:
            conn.execute(f"SELECT 1 FROM {control.namespace()}.chat_policies LIMIT 1")
    except Exception:
        raise HTTPException(503, "Database unavailable")
    return {
        "status": "ok",
        "version": settings().get("SEMANTIC_VERSION", "puroseka-1.1.0"),
        "service": "chat",
        "model": settings().get("MISTRAL_MODEL", "ministral-8b-latest"),
        "mistral_configured": bool(settings().get("MISTRAL_API_KEY")),
    }


@app.post("/api/inference")
async def inference(request: InferenceRequest, user=Depends(auth.admin_user)):
    session = session_token.set(user.get("_session_token", ""))
    token = principal.set(user["username"])
    try:
        async with _inflight:
            result = await infer(
                request.question,
                request.mode,
                [m.model_dump() for m in request.history],
            )
        if result["status"] == "limited":
            raise HTTPException(
                429,
                result["answer"],
                headers={"Retry-After": str(result["retry_after"])},
            )
        return result
    finally:
        principal.reset(token)
        session_token.reset(session)


@app.get("/api/{path:path}")
def ontology_proxy(path: str, request: Request, user=Depends(auth.admin_user)):
    allowed = {
        "catalog",
        "manifest",
        "schema",
        "ranking-manifest",
        "rankings",
        "ontology.ttl",
        "translations",
    }
    if path not in allowed and not (
        path.startswith("tables/") and len(path.split("/")) == 2
    ):
        raise HTTPException(404)
    from urllib.parse import urlencode

    route = "/api/" + path
    if request.query_params:
        route += "?" + urlencode(list(request.query_params.multi_items()))
    content, content_type = call(
        "ontology", "GET", route, token=user["_session_token"], raw=True
    )
    return Response(content, media_type=content_type)


static = Path(__file__).resolve().parents[2] / "web/dist"
if static.exists():
    app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        return FileResponse(static / "index.html")
