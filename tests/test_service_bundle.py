"""Source uploads exclude local secrets/data and unrelated services."""

import io
import subprocess
import tarfile

import pytest
from scripts import service_bundle


@pytest.fixture
def source_repo(tmp_path, monkeypatch):
    files = {
        "services/chat/main.py": "# chat\n",
        "services/chat/.python-version": "3.15\n",
        "services/chat/requirements.txt": "-r ../../shared/requirements.txt\n",
        "services/login/main.py": "# unrelated service\n",
        "shared/config.py": "# config\n",
        "shared/requirements.txt": "fastapi==0.135.1\n",
        "infra/heroku/start.py": "# start\n",
        "LICENSE": "license\n",
        "NOTICE": "notice\n",
        "THIRD_PARTY.md": "credits\n",
        ".env": "PRIVATE_SECRET=never-upload-this\n",
        "web/index.html": "frontend\n",
    }
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.test",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "services/chat/local-dump.json").write_text('{"private":"untracked"}')
    monkeypatch.setattr(service_bundle, "ROOT", tmp_path)
    return tmp_path


def test_upload_contains_only_selected_tracked_source(source_repo):
    with tarfile.open(
        fileobj=io.BytesIO(service_bundle.bundle("chat", "HEAD"))
    ) as archive:
        names = archive.getnames()
        assert "services/chat/main.py" in names and "shared/config.py" in names
        assert ".env" not in names and "web/index.html" not in names
        assert "services/login/main.py" not in names
        assert "services/chat/local-dump.json" not in names
        assert (
            archive.extractfile("requirements.txt").read()
            == b"-r services/chat/requirements.txt\n"
        )
        assert archive.extractfile(".python-version").read() == b"3.15\n"


def test_upload_refuses_tracked_symlinks(source_repo):
    (source_repo / "services/chat/link.py").symlink_to("../../.env")
    subprocess.run(["git", "add", "services/chat/link.py"], cwd=source_repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.test",
            "commit",
            "-qm",
            "symlink",
        ],
        cwd=source_repo,
        check=True,
    )
    with pytest.raises(ValueError, match="regular tracked source"):
        service_bundle.bundle("chat", "HEAD")
