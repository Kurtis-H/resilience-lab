"""Fixtures: the stack under test, its fault injector, and helpers.

TOXIPROXY_URL  Toxiproxy's API (default http://127.0.0.1:8474)
API_URL        the booking API, direct (default http://127.0.0.1:8000)
WEB_URL        booking-web's production build, served (default http://127.0.0.1:4173)
DATABASE_URL   Postgres, direct, not through the proxy (for locks and checks)

CI starts all of it (see .github/workflows/resilience.yml). Only ever point
these at your own local or CI stack.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ADMIN = {"X-API-Key": "key-admin"}


class Toxiproxy:
    """The few Toxiproxy API calls the experiments need."""

    def __init__(self, client: httpx.Client) -> None:
        self.client = client

    def disable(self, proxy: str) -> None:
        self.client.post(f"/proxies/{proxy}", json={"enabled": False}).raise_for_status()

    def enable(self, proxy: str) -> None:
        self.client.post(f"/proxies/{proxy}", json={"enabled": True}).raise_for_status()

    def toxic(self, proxy: str, kind: str, stream: str = "downstream", **attributes: int) -> None:
        body = {"type": kind, "stream": stream, "toxicity": 1.0, "attributes": attributes}
        self.client.post(f"/proxies/{proxy}/toxics", json=body).raise_for_status()

    def reset(self) -> None:
        """Every proxy enabled, every toxic removed."""
        self.client.post("/reset").raise_for_status()


@pytest.fixture(scope="session")
def toxi() -> Iterator[Toxiproxy]:
    url = os.environ.get("TOXIPROXY_URL", "http://127.0.0.1:8474")
    with httpx.Client(base_url=url, timeout=10) as client:
        yield Toxiproxy(client)


@pytest.fixture(scope="session")
def api() -> Iterator[httpx.Client]:
    with httpx.Client(base_url=os.environ.get("API_URL", "http://127.0.0.1:8000"), timeout=30) as c:
        yield c


@pytest.fixture(scope="session")
def web_url() -> str:
    return os.environ.get("WEB_URL", "http://127.0.0.1:4173")


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "postgresql://postgres@127.0.0.1:5432/booking")
    return url.replace("postgresql+psycopg://", "postgresql://")


def wait_until_serving(api: httpx.Client, within: float = 30) -> float:
    """Seconds until the API serves a database-backed request again."""
    start = time.monotonic()
    while time.monotonic() - start < within:
        try:
            if api.get("/events", params={"limit": 1}, timeout=5).status_code == 200:
                return time.monotonic() - start
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise AssertionError(f"the API didn't recover within {within} s")


@pytest.fixture
def healthy_stack(toxi: Toxiproxy, api: httpx.Client) -> Iterator[None]:
    """Each experiment starts and ends with no faults and a serving API."""
    toxi.reset()
    wait_until_serving(api)
    yield
    toxi.reset()
    wait_until_serving(api)


@pytest.fixture
def new_event(api: httpx.Client):
    """Create an event through the admin API; returns (event id, seat ids)."""

    def make(seats: int) -> tuple[int, list[int]]:
        created = api.post(
            "/admin/events",
            headers=ADMIN,
            json={
                "name": "Resilience event",
                "venue": "Test hall",
                "starts_at": datetime(2027, 6, 1, 19, tzinfo=UTC).isoformat(),
                "seats": seats,
                "price_cents": 3500,
            },
        )
        assert created.status_code == 201, created.text
        event_id = created.json()["id"]
        page = api.get(f"/events/{event_id}/seats", params={"limit": 100}).json()
        return event_id, [s["id"] for s in page["items"]]

    return make
