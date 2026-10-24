import asyncio
import json
import time
import httpx
from shared.config import settings
from services.chat import control, tracing
from services.chat.metering import principal, reservation_size

_lock = asyncio.Lock()
_last_call = 0.0
QUERY_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "sql": {"anyOf": [{"type": "string", "maxLength": 12000}, {"type": "null"}]},
        "reason": {"type": "string", "maxLength": 240},
    },
    "required": ["sql", "reason"],
    "additionalProperties": False,
}


def complete_query_plan(content):
    """Only a complete, bounded plan can survive a provider length signal."""
    try:
        value = json.loads(content)
    except (ValueError, TypeError):
        return False
    return (
        isinstance(value, dict)
        and set(value) == {"sql", "reason"}
        and (
            value["sql"] is None
            or isinstance(value["sql"], str)
            and len(value["sql"]) <= 12000
        )
        and isinstance(value["reason"], str)
        and len(value["reason"]) <= 240
    )


class ModelError(Exception):
    pass


async def complete(messages, json_mode=False):
    cfg = settings()
    key = cfg.get("MISTRAL_API_KEY")
    if not key:
        raise ModelError("Mistral is not configured. Set the server-side API key.")
    payload = {
        "model": cfg.get("MISTRAL_MODEL", "ministral-8b-latest"),
        "messages": messages,
        "temperature": 0,
        "max_tokens": 4096 if json_mode else 1800,
    }
    if json_mode:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "query_plan",
                "schema": QUERY_PLAN_SCHEMA,
                "strict": True,
            },
        }
    call = tracing.start("model", tracing.stage_name.get(), payload)
    await tracing.publish()
    username = principal.get()
    reservation = None
    charged = None

    def usage_charge(value):
        nonlocal charged
        charged = value

    try:
        if username:
            amount = await asyncio.to_thread(
                reservation_size, messages, payload["max_tokens"]
            )
            reservation = await asyncio.to_thread(
                control.reserve,
                username,
                amount,
            )
        content, usage = await _request(payload, key, usage_charge)
        tracing.finish(call, {"content": content, "usage": usage})
        await tracing.publish()
        return content, usage
    except BaseException as exc:
        message = (
            str(exc)
            if isinstance(exc, (ModelError, control.BudgetExceeded))
            else "The model request did not complete."
        )
        tracing.finish(
            call,
            {"error": message},
            "blocked" if isinstance(exc, control.BudgetExceeded) else "error",
        )
        await tracing.publish()
        raise
    finally:
        if reservation:
            await asyncio.shield(
                asyncio.to_thread(control.settle, username, reservation, charged)
            )


async def _request(payload, key, record_usage):
    global _last_call
    async with _lock:
        async with httpx.AsyncClient(timeout=90) as client:
            for attempt in range(4):
                await asyncio.sleep(max(0, 1.1 - (time.monotonic() - _last_call)))
                try:
                    _last_call = time.monotonic()
                    r = await client.post(
                        "https://api.mistral.ai/v1/chat/completions",
                        headers={"Authorization": "Bearer " + key},
                        json=payload,
                    )
                except httpx.HTTPError as e:
                    raise ModelError("Mistral could not be reached; try again.") from e
                if r.status_code == 429 and attempt < 3:
                    await asyncio.sleep(2 ** (attempt + 1))
                    continue
                if r.status_code != 200:
                    if r.status_code in {400, 401, 402, 403, 404, 422, 429}:
                        record_usage(0)
                    msg = {
                        401: "Mistral rejected the API key.",
                        402: "The selected model requires unavailable credit.",
                        403: "The selected model is not available to this account.",
                        429: "Mistral sandbox quota or rate limit reached.",
                    }.get(
                        r.status_code, f"Mistral request failed (HTTP {r.status_code})."
                    )
                    raise ModelError(msg)
                d = r.json()
                usage = d.get("usage", {})
                total = usage.get("total_tokens")
                if isinstance(total, int) and total >= 0:
                    record_usage(total)
                choice = d["choices"][0]
                content = choice["message"]["content"]
                plan_mode = (
                    payload.get("response_format", {}).get("type") == "json_schema"
                )
                if choice.get("finish_reason") == "length" and not (
                    plan_mode and complete_query_plan(content)
                ):
                    raise ModelError(
                        "The model returned an incomplete response at its output limit. Try again."
                    )
                if plan_mode and not complete_query_plan(content):
                    raise ModelError("The model returned an invalid query plan.")
                return content.strip(), usage
    raise ModelError("Mistral did not return a completion.")
