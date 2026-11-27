"""Create the Toxiproxy proxies the stack runs through.

    python scripts/proxies.py

  db   127.0.0.1:15432 -> Postgres on 127.0.0.1:5432   (the API connects here)
  api  127.0.0.1:18000 -> the API on 127.0.0.1:8000    (booking-web connects here)

Everything is on the CI runner's loopback interface; nothing is exposed.
"""

from __future__ import annotations

import os

import httpx

TOXIPROXY = os.environ.get("TOXIPROXY_URL", "http://127.0.0.1:8474")
PROXIES = [
    {"name": "db", "listen": "127.0.0.1:15432", "upstream": "127.0.0.1:5432"},
    {"name": "api", "listen": "127.0.0.1:18000", "upstream": "127.0.0.1:8000"},
]


def main() -> None:
    with httpx.Client(base_url=TOXIPROXY, timeout=10) as toxi:
        for proxy in PROXIES:
            created = toxi.post("/proxies", json={**proxy, "enabled": True})
            if created.status_code == 409:  # already there: make sure it's enabled
                toxi.post(f"/proxies/{proxy['name']}", json={"enabled": True}).raise_for_status()
            else:
                created.raise_for_status()
            print(f"proxy {proxy['name']}: {proxy['listen']} -> {proxy['upstream']}")


if __name__ == "__main__":
    main()
