"""Request-local review data: application inputs and visible outputs, no headers."""

import asyncio
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar

calls = ContextVar("review_calls", default=None)
progress = ContextVar("review_progress", default=None)
stage_name = ContextVar("review_stage", default="Mistral completion")


def activity(collector=None):
    """Public execution metadata, deliberately excluding arguments and results."""
    return [
        {
            k: entry[k]
            for k in ("sequence", "kind", "name", "status", "latency_ms")
            if k in entry
        }
        for entry in (calls.get() if collector is None else collector) or []
    ]


async def publish():
    callback = progress.get()
    if callback:
        try:
            await callback(activity())
        except Exception:
            # Progress is best effort and must not interrupt inference or accounting.
            logging.getLogger(__name__).warning("Could not save execution progress")


@contextmanager
def stage(name):
    token = stage_name.set(name)
    try:
        yield
    finally:
        stage_name.reset(token)


def start(kind, name, inputs):
    collector = calls.get()
    if collector is None:
        return None
    entry = {
        "sequence": len(collector) + 1,
        "kind": kind,
        "name": name,
        "input": inputs,
        "output": None,
        "status": "running",
        "_started": time.monotonic(),
    }
    collector.append(entry)
    return entry


def finish(entry, output, status="ok"):
    if entry is not None:
        entry["latency_ms"] = round((time.monotonic() - entry.pop("_started")) * 1000)
        entry.update(output=output, status=status)


async def tool(name, inputs, function, *args):
    from app.query import QueryRejected

    entry = start("tool", name, inputs)
    await publish()
    try:
        output = await asyncio.to_thread(function, *args)
    except QueryRejected as exc:
        finish(entry, {"error": str(exc)}, "error")
        await publish()
        raise
    except BaseException:
        # Connection exceptions can contain credentials or host details.
        finish(entry, {"error": "The database tool did not complete."}, "error")
        await publish()
        raise
    finish(entry, output)
    await publish()
    return output
