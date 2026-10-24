"""Package one service from an audited Git revision for Heroku's Build API."""

import io
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def bundle(service, revision):
    if service not in {"chat", "ontology", "login"}:
        raise ValueError("Unknown service")
    raw = subprocess.check_output(
        [
            "git",
            "archive",
            revision,
            f"services/{service}",
            "shared",
            "infra/heroku/start.py",
            "LICENSE",
            "NOTICE",
            "THIRD_PARTY.md",
        ],
        cwd=ROOT,
    )
    files = {}
    with tarfile.open(fileobj=io.BytesIO(raw)) as source:
        for member in source.getmembers():
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError("Only regular tracked source files may be deployed")
            files[member.name] = source.extractfile(member).read()
    # Heroku requires root build files. They exist only in this upload bundle;
    # the repository keeps dependency/runtime ownership with each service.
    files["requirements.txt"] = f"-r services/{service}/requirements.txt\n".encode()
    files[".python-version"] = files[f"services/{service}/.python-version"]
    files["Procfile"] = b"web: python infra/heroku/start.py\n"
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as target:
        for name, content in sorted(files.items()):
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(content), 0o644
            target.addfile(member, io.BytesIO(content))
    return output.getvalue()


def upload(api, app_id, service, revision):
    import httpx

    content = bundle(service, revision)
    response = api.post(f"/apps/{app_id}/sources")
    response.raise_for_status()
    source = response.json()["source_blob"]
    # Separate client: never forward Heroku's Authorization header to storage.
    with httpx.Client(timeout=60) as storage:
        response = storage.put(source["put_url"], content=content)
        response.raise_for_status()
    return source["get_url"]
