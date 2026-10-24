"""Local prompt counting for the deployed Ministral 8B revision.

The vocabulary is public and checksum-pinned. Prompt text is counted locally;
the vocabulary host receives no prompts, source records or credentials.
"""

from functools import lru_cache
import base64
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile
from threading import Lock

import httpx
from app.config import settings

REVISION = "5b26027e7b19eeb4b7352e1fed3926375dd2cb4d"
SHA256 = "600bb27946565481ecf51ba8aee252e49b9a68507866080ac9c30185bb312843"
URL = f"https://huggingface.co/mistralai/Ministral-3-8B-Instruct-2512/resolve/{REVISION}/tekken.json"
SUPPORTED = {"ministral-8b-latest", "ministral-8b-2512"}
_lock = Lock()


@lru_cache(maxsize=1)
def _alias_matches():
    # Do not apply a pinned vocabulary if the provider moves the latest alias.
    key = settings().get("MISTRAL_API_KEY")
    if not key:
        return False
    response = httpx.get(
        "https://api.mistral.ai/v1/models/ministral-8b-latest",
        headers={"Authorization": "Bearer " + key},
        timeout=15,
    )
    response.raise_for_status()
    return response.json().get("name") == "ministral-8b-2512"


@lru_cache(maxsize=1)
def _tokenizer():
    import tiktoken

    path = Path(tempfile.gettempdir()) / ("coa-tekken-" + SHA256 + ".json")
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != SHA256:
        data = bytearray()
        with httpx.stream("GET", URL, follow_redirects=True, timeout=45) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > 20 * 1024 * 1024:
                    raise ValueError("Tokenizer exceeds the expected size")
        if hashlib.sha256(data).hexdigest() != SHA256:
            raise ValueError("Tokenizer checksum mismatch")
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
            temporary.write(data)
        try:
            os.replace(temporary.name, path)
        finally:
            Path(temporary.name).unlink(missing_ok=True)
    data = json.loads(path.read_text())
    config = data["config"]
    # Tekken uses tiktoken for ordinary text and reserves a prefix of IDs for
    # control tokens. We only count text here; metering adds template headroom.
    regular = config["default_vocab_size"] - config["default_num_special_tokens"]
    ranks = {
        base64.b64decode(token["token_bytes"]): token["rank"]
        for token in data["vocab"]
        if token["rank"] < regular
    }
    return tiktoken.Encoding(
        name="ministral-8b-2512",
        pat_str=config["pattern"],
        mergeable_ranks=ranks,
        special_tokens={},
    )


def prompt_tokens(messages, model):
    if model not in SUPPORTED:
        return None
    try:
        if model.endswith("-latest") and not _alias_matches():
            return None
        with _lock:
            tokenizer = _tokenizer()
        return sum(len(tokenizer.encode_ordinary(m["content"])) + 64 for m in messages)
    except Exception:
        logging.getLogger(__name__).warning(
            "Local tokenizer unavailable; using the conservative byte reservation."
        )
        return None
