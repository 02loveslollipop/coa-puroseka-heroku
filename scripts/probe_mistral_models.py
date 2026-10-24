"""Minimal live quota checks; does not change the configured or deployed model."""

import datetime
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx
from app.config import settings


def run():
    cfg = settings()
    report = {
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "scope": "Every listed chat-completion model ID, including aliases; minimal requests, no configuration changes.",
        "results": [],
        "non_chat_models": [],
    }
    target = ROOT / "artifacts/mistral-model-quota.json"
    with httpx.Client(
        headers={"Authorization": "Bearer " + cfg["MISTRAL_API_KEY"]}, timeout=45
    ) as client:
        response = client.get("https://api.mistral.ai/v1/models")
        response.raise_for_status()
        models = response.json()["data"]
        chat = [m for m in models if m.get("capabilities", {}).get("completion_chat")]
        report["listed_model_ids"] = len(models)
        report["non_chat_models"] = [
            {
                "id": m["id"],
                "name": m.get("name"),
                "capabilities": m.get("capabilities", {}),
            }
            for m in models
            if m not in chat
        ]
        print(
            f"Checking {len(chat)} chat model IDs; {len(models) - len(chat)} non-chat IDs excluded from chat probes.",
            flush=True,
        )
        for model in chat:
            start = time.monotonic()
            entry = {
                "model": model["id"],
                "canonical_name": model.get("name"),
                "context_length": model.get("max_context_length"),
                "capabilities": model.get("capabilities", {}),
            }
            try:
                r = client.post(
                    "https://api.mistral.ai/v1/chat/completions",
                    json={
                        "model": model["id"],
                        "messages": [{"role": "user", "content": "Reply with OK."}],
                        "max_tokens": 16,
                    },
                )
                entry.update(
                    http_status=r.status_code,
                    rate_headers={
                        k: v
                        for k, v in r.headers.items()
                        if "ratelimit" in k.lower() or k.lower() == "retry-after"
                    },
                )
                try:
                    body = r.json()
                except ValueError:
                    body = {}
                if r.status_code == 200:
                    choice = body.get("choices", [{}])[0]
                    entry.update(
                        status="available",
                        finish_reason=choice.get("finish_reason"),
                        response_excerpt=str(
                            choice.get("message", {}).get("content", "")
                        )[:160],
                        usage=body.get("usage", {}),
                        resolved_model=body.get("model"),
                    )
                else:
                    zero = any(
                        v == "0"
                        for k, v in entry["rate_headers"].items()
                        if "limit-req" in k and "remaining" not in k
                    )
                    entry.update(
                        status="zero_quota"
                        if r.status_code == 429 and zero
                        else "rate_limited"
                        if r.status_code == 429
                        else "restricted"
                        if r.status_code in (401, 402, 403)
                        else "request_error",
                        error=body.get("message", body.get("error", str(body))),
                    )
            except httpx.HTTPError as e:
                entry.update(status="transport_error", error=type(e).__name__)
            entry["latency_ms"] = round((time.monotonic() - start) * 1000)
            report["results"].append(entry)
            target.write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(
                entry["model"],
                entry["status"],
                entry.get("http_status"),
                json.dumps(entry.get("rate_headers", {})),
                flush=True,
            )
            time.sleep(1.1)
    print("Saved:", target, flush=True)


if __name__ == "__main__":
    run()
