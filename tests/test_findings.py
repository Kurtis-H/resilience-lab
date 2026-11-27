"""Each finding, reproduced by injecting its fault into the running stack.

A finding marked open or accepted must still reproduce; one marked fixed must
not (that's the retest). The evidence goes to results/evidence.md, which CI
adds to the job summary.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import httpx
import psycopg
import pytest
from playwright.sync_api import sync_playwright

from findings import BY_ID

pytestmark = pytest.mark.usefixtures("healthy_stack")

RESULTS = Path(__file__).resolve().parents[1] / "results"
_evidence: list[str] = []


@pytest.fixture(scope="session", autouse=True)
def write_evidence():
    yield
    if _evidence:
        RESULTS.mkdir(exist_ok=True)
        (RESULTS / "evidence.md").write_text(
            "| Finding | Status | Reproduces | Evidence |\n|---|---|---|---|\n" + "".join(_evidence)
        )


def verdict(finding_id: str, weak: bool, evidence: str) -> None:
    f = BY_ID[finding_id]
    _evidence.append(f"| {f.id} {f.title} | {f.status} | {'yes' if weak else 'no'} | {evidence} |\n")
    if f.reproduced:
        assert weak, f"{f.id} is {f.status}, so it should reproduce, but didn't: {evidence}"
    else:
        assert not weak, f"{f.id} is marked fixed, but still reproduces: {evidence}"


def test_rl01_a_locked_table_stalls_requests(api: httpx.Client, database_url: str, new_event) -> None:
    event_id, _ = new_event(2)
    patience = 20.0
    locked = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        # Like a long migration: an exclusive lock on seats, held until released.
        with psycopg.connect(database_url) as conn:
            conn.execute("LOCK TABLE seats IN ACCESS EXCLUSIVE MODE")
            locked.set()
            release.wait(patience + 10)

    holder = threading.Thread(target=hold_lock, daemon=True)
    holder.start()
    assert locked.wait(10), "couldn't take the lock"
    start = time.monotonic()
    try:
        response = api.get(f"/events/{event_id}/seats", params={"limit": 1}, timeout=patience)
        outcome = f"{response.status_code} after {time.monotonic() - start:.1f} s"
        stalled = False
    except httpx.TimeoutException:
        outcome = f"no answer after {patience:.0f} s"
        stalled = True
    finally:
        release.set()
        holder.join(15)
    verdict("RL-01", stalled, f"`GET /events/{{id}}/seats` while `seats` is locked: {outcome}")


def test_rl02_a_database_outage_returns_a_bare_500(api: httpx.Client, toxi) -> None:
    toxi.disable("db")
    start = time.monotonic()
    response = api.get("/events", params={"limit": 1})
    took = time.monotonic() - start
    kind = response.headers.get("content-type", "none")
    retry = response.headers.get("retry-after")
    graceful = response.status_code == 503 and kind.startswith("application/problem+json") and bool(retry)
    verdict(
        "RL-02",
        not graceful,
        f"`GET /events` with the database down: {response.status_code}, `{kind}`, "
        f"Retry-After `{retry or 'none'}`, in {took:.2f} s",
    )


def test_rl03_nothing_reports_the_database_is_down(api: httpx.Client, toxi) -> None:
    toxi.disable("db")
    health = api.get("/health").status_code
    ready = api.get("/ready").status_code
    verdict(
        "RL-03",
        ready != 503,
        f"with the database down: `/health` {health}, `/ready` {ready}",
    )


def test_rl04_the_web_app_waits_forever_on_a_silent_api(toxi, web_url: str) -> None:
    # The API's responses never arrive: connections open, nothing comes back.
    toxi.toxic("api", "timeout", stream="downstream", timeout=0)
    patience_ms = 30_000
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("PW_EXECUTABLE") or None)
        try:
            page = browser.new_page()
            page.goto(web_url, wait_until="domcontentloaded")
            start = time.monotonic()
            error = page.get_by_text("couldn't load", exact=False)
            try:
                error.first.wait_for(timeout=patience_ms)
                outcome = f"error shown after {time.monotonic() - start:.0f} s"
                waits = False
            except Exception:  # noqa: BLE001 - Playwright's TimeoutError
                outcome = f"still loading after {patience_ms // 1000} s, no error shown"
                waits = True
        finally:
            browser.close()
    verdict("RL-04", waits, f"events page with a silent API: {outcome}")
