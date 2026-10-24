"""Meter every model call within an authenticated request, including synthesis."""

from contextvars import ContextVar

principal = ContextVar("metered_principal", default=None)


def reservation_size(messages, max_tokens):
    # Conservative byte-based input bound for byte-level tokenizers, plus chat
    # template overhead. Provider usage replaces it after a successful response.
    return (
        sum(len(m["content"].encode("utf-8")) + 64 for m in messages)
        + 1024
        + max_tokens
    )
