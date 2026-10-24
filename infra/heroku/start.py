"""One web process per app, always using Heroku's supplied port."""

import os
import sys
from pathlib import Path
import uvicorn

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[2])
    if Path(__file__).name == "start.py" and Path(__file__).parent.name == "heroku"
    else str(Path(__file__).resolve().parent),
)

service = os.environ.get("COA_SERVICE", "chat")
if service not in {"chat", "ontology", "login"}:
    raise SystemExit("Invalid COA_SERVICE")
uvicorn.run(
    f"services.{service}.main:app",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", "8000")),
    workers=1,
    proxy_headers=True,
    forwarded_allow_ips="*",
)
