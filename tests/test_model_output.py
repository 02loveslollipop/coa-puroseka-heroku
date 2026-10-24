"""A truncated model response is charged and reported without parsing partial JSON."""

import asyncio
import httpx
import pytest
from services.chat import llm


def test_truncated_response_retains_actual_usage(monkeypatch):
    charged = []
    original = httpx.AsyncClient
    response = {
        "choices": [
            {"finish_reason": "length", "message": {"content": '{"sql":"unfinished'}}
        ],
        "usage": {"total_tokens": 1800},
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=response))
    monkeypatch.setattr(
        llm.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=transport, **kwargs),
    )
    monkeypatch.setattr(llm, "_last_call", 0)
    with pytest.raises(llm.ModelError, match="exceeded its response length"):
        asyncio.run(llm._request({"model": "test"}, "test-key", charged.append))
    assert charged == [1800]
