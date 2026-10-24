"""Password/session service. No model key or ontology tooling."""

from fastapi import FastAPI, Depends, Request
from pydantic import BaseModel, Field
from psycopg import Error as DatabaseError
from app import local_auth
from app.control import control_connection
from app.database import namespace
from shared.security import require_service, headers, database_unavailable

app = FastAPI(
    title="COA login service", docs_url=None, redoc_url=None, openapi_url=None
)
app.middleware("http")(headers)
app.add_exception_handler(DatabaseError, database_unavailable)


def trusted(request: Request):
    require_service(request, "LOGIN_SERVICE_TOKEN")


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=1024)
    client_ip: str = Field(max_length=128)


class Session(BaseModel):
    token: str = Field(min_length=1, max_length=256)


@app.get("/api/health")
def health():
    with control_connection() as conn:
        conn.execute(f"SELECT 1 FROM {namespace()}.auth_accounts LIMIT 1")
    return {"status": "ok", "service": "login"}


@app.post("/internal/login", dependencies=[Depends(trusted)])
def login(body: Credentials):
    token, user = local_auth.login(body.username, body.password, body.client_ip)
    return {"token": token, "user": user}


@app.post("/internal/introspect", dependencies=[Depends(trusted)])
def introspect(body: Session):
    return local_auth.identity(body.token)


@app.post("/internal/logout", dependencies=[Depends(trusted)])
def logout(body: Session):
    local_auth.revoke(body.token)
    return {"status": "ok"}
