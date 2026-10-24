"""Bind the existing chat Eco app to a DNS-only CNAME and Heroku ACM."""

import json
import netrc
from pathlib import Path
import sys

import httpx
from dotenv import set_key

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.cloudflare_cli import zone, dns_only, FRONTEND, BACKEND

ROOT = Path(__file__).resolve().parents[1]


def configure():
    path = ROOT / ".deploy/heroku-services.json"
    state = json.loads(path.read_text())
    chat = state["services"]["chat"]
    token = netrc.netrc().authenticators("api.heroku.com")[2]
    with httpx.Client(
        base_url="https://api.heroku.com",
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.heroku+json; version=3",
        },
        timeout=30,
    ) as api:
        app = "/apps/" + chat["id"]
        formation = api.get(app + "/formation")
        formation.raise_for_status()
        if len(formation.json()) != 1 or formation.json()[0]["size"].lower() != "eco":
            raise RuntimeError("An existing Eco web app is required")
        response = api.get(app + "/domains")
        response.raise_for_status()
        domains = [d for d in response.json() if d["hostname"] == BACKEND]
        if domains:
            domain = domains[0]
        else:
            response = api.post(
                app + "/domains", json={"hostname": BACKEND, "sni_endpoint": None}
            )
            response.raise_for_status()
            domain = response.json()
        if not domain.get("cname"):
            raise RuntimeError("Heroku has not assigned a DNS target yet")
        dns = dns_only(zone(), BACKEND, domain["cname"])
        response = api.post(app + "/acm")
        response.raise_for_status()
    state["public_api_origin"] = "https://" + BACKEND
    state["frontend_origin"] = "https://" + FRONTEND
    chat["custom_url"] = state["public_api_origin"]
    path.write_text(json.dumps(state, indent=2) + "\n")
    path.chmod(0o600)
    set_key(ROOT / ".env", "PUBLIC_ORIGIN", state["frontend_origin"])
    (ROOT / ".env").chmod(0o600)
    print(
        json.dumps(
            {
                "api": state["public_api_origin"],
                "dns_proxied": dns["proxied"],
                "tls": "Heroku ACM enabled",
            }
        )
    )


if __name__ == "__main__":
    configure()
