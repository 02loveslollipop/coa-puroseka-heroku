"""Meter every model call within an authenticated request, including synthesis."""

from contextvars import ContextVar
from app.config import settings
from app.tokenization import prompt_tokens

principal = ContextVar("metered_principal", default=None)


def reservation_size(messages, max_tokens):
    # Reserve the local model's input count, template safety margin and maximum
    # output. Provider usage replaces this reservation after a response.
    count = prompt_tokens(
        messages, settings().get("MISTRAL_MODEL", "ministral-8b-latest")
    )
    if count is None:
        count = sum(len(m["content"].encode("utf-8")) + 64 for m in messages)
    return count + 1024 + max_tokens
