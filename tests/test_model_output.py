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
    with pytest.raises(llm.ModelError, match="incomplete response"):
        asyncio.run(llm._request({"model": "test"}, "test-key", charged.append))
    assert charged == [1800]


def test_structured_query_plan_has_separate_output_budget(monkeypatch):
    seen = []

    async def request(payload, key, record_usage):
        seen.append(payload)
        return '{"sql":"SELECT 1","reason":"Count"}', {"total_tokens": 50}

    monkeypatch.setitem(llm.settings(), "MISTRAL_API_KEY", "test-key")
    monkeypatch.setattr(llm, "_request", request)
    token = llm.principal.set("")
    try:
        asyncio.run(llm.complete([{"role": "user", "content": "test"}], True))
        asyncio.run(llm.complete([{"role": "user", "content": "test"}], False))
    finally:
        llm.principal.reset(token)
    assert seen[0]["max_tokens"] == 4096
    assert seen[0]["response_format"]["type"] == "json_schema"
    assert seen[0]["response_format"]["json_schema"]["schema"] == llm.QUERY_PLAN_SCHEMA
    assert seen[1]["max_tokens"] == 1800
    assert "response_format" not in seen[1]


@pytest.mark.parametrize(
    "content,accepted",
    [
        ('{"sql":"SELECT 1","reason":"Count"}   ', True),
        ('{"sql":null,"reason":"Unavailable"}', True),
        ('{"sql":"SELECT unfinished', False),
        ('{"sql":2,"reason":"Count"}', False),
        ('{"sql":"SELECT 1","reason":"Count","extra":"ignored"}', False),
        ('{"sql":"SELECT 1","reason":"' + "x" * 241 + '"}', False),
    ],
)
def test_length_signal_only_accepts_complete_bounded_plan(
    monkeypatch, content, accepted
):
    charged = []
    original = httpx.AsyncClient
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "length", "message": {"content": content}}
                ],
                "usage": {"total_tokens": 4096},
            },
        )
    )
    monkeypatch.setattr(
        llm.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=transport, **kwargs),
    )
    monkeypatch.setattr(llm, "_last_call", 0)
    payload = {"model": "test", "response_format": {"type": "json_schema"}}
    if accepted:
        result, _ = asyncio.run(llm._request(payload, "test-key", charged.append))
        assert result == content.strip()
    else:
        with pytest.raises(llm.ModelError, match="incomplete response"):
            asyncio.run(llm._request(payload, "test-key", charged.append))
    assert charged == [4096]
