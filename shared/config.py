import json
import os
from functools import lru_cache
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


@lru_cache
def settings():
    return dict(os.environ)


ARTIFACTS = Path(
    os.environ.get(
        "ARTIFACT_DIR", str(Path(__file__).resolve().parents[1] / "artifacts")
    )
)


def artifact(name):
    if settings().get("ARTIFACT_STORE") == "database":
        from shared.database import connection, namespace

        with connection() as conn:
            row = conn.execute(
                f"SELECT payload FROM {namespace()}.ontology_versions WHERE name=%s",
                (name,),
            ).fetchone()
        if not row:
            raise ValueError("Ontology artifact unavailable")
        return row["payload"]
    return json.loads((ARTIFACTS / name).read_text())
