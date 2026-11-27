"""Experiments where the system already behaves well.

Kept as tests so that a change which breaks them fails CI. Each one has a
control step where that makes sense, proving the fault really was injected.
"""

from __future__ import annotations

import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import psycopg
import pytest
from playwright.sync_api import sync_playwright

from tests.conftest import wait_until_serving

pytestmark = pytest.mark.usefixtures("healthy_stack")


def test_the_api_recovers_by_itself_when_the_database_returns(api: httpx.Client, toxi) -> None:
    toxi.disable("db")
    assert api.get("/events", params={"limit": 1}).status_code >= 500, "control: the outage is real"
    time.sleep(3)
    toxi.enable("db")
    recovered_in = wait_until_serving(api, within=10)
    print(f"recovered {recovered_in:.2f} s after the database came back, with no restart")


def test_database_latency_slows_requests_but_none_fail(api: httpx.Client, toxi) -> None:
    def timed() -> tuple[int, float]:
        start = time.monotonic()
        code = api.get("/events", params={"limit": 5}).status_code
        return code, time.monotonic() - start

    before = [timed()[1] for _ in range(10)]
    toxi.toxic("db", "latency", latency=50)
    during = [timed() for _ in range(20)]
    assert all(code == 200 for code, _ in during), [code for code, _ in during]
    slow, fast = statistics.median(t for _, t in during), statistics.median(before)
    assert slow > fast + 0.04, f"control: the latency was applied ({fast:.3f} -> {slow:.3f} s)"
    print(f"median GET /events: {fast * 1000:.0f} ms -> {slow * 1000:.0f} ms with 50 ms database latency")


def test_dropped_database_connections_never_leave_half_an_order(
    api: httpx.Client, toxi, database_url: str, new_event
) -> None:
    """Connections reset while orders are in flight: every order is all or nothing."""
    event_id, seats = new_event(40)
    buyers = [{"X-API-Key": f"key-user-{n}"} for n in range(120, 140)]

    def order(i: int) -> tuple[int, int | None]:
        if i == 6:
            toxi.toxic("db", "reset_peer", stream="upstream", timeout=0)
        if i == 13:
            toxi.reset()
        try:
            r = api.post("/orders", headers=buyers[i], json={"seat_ids": seats[i * 2 : i * 2 + 2]})
        except httpx.HTTPError as e:
            print("transport error:", type(e).__name__, e)
            return -1, None
        return r.status_code, r.json().get("id") if r.status_code == 201 else None

    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(order, range(20)))
    toxi.reset()
    wait_until_serving(api)
    codes = [code for code, _ in results]
    assert any(code == 201 for code in codes), f"no order succeeded: {codes}"
    assert any(code != 201 for code in codes), f"control: the resets hit no order: {codes}"

    with psycopg.connect(database_url) as conn:
        sold = conn.execute(
            "SELECT count(*) FROM seats WHERE event_id = %s AND status = 'sold'", (event_id,)
        ).fetchone()[0]
        in_orders = conn.execute(
            "SELECT count(*) FROM order_items oi JOIN seats s ON s.id = oi.seat_id WHERE s.event_id = %s",
            (event_id,),
        ).fetchone()[0]
        empty_orders = conn.execute(
            "SELECT count(*) FROM orders o WHERE NOT EXISTS "
            "(SELECT 1 FROM order_items oi WHERE oi.order_id = o.id)"
        ).fetchone()[0]
        confirmed = [oid for _, oid in results if oid is not None]
        found = conn.execute("SELECT count(*) FROM orders WHERE id = ANY(%s)", (confirmed,)).fetchone()[0]
    assert sold == in_orders, f"{sold} seats sold but {in_orders} in orders"
    assert empty_orders == 0, f"{empty_orders} orders with no seats"
    assert found == len(confirmed), "an order the API confirmed isn't in the database"
    print(f"order outcomes: {sorted(codes)}; {sold} seats sold, all accounted for")


def test_the_web_app_shows_an_error_when_the_api_is_down(toxi, web_url: str) -> None:
    toxi.disable("api")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("PW_EXECUTABLE") or None)
        try:
            page = browser.new_page()
            page.goto(web_url, wait_until="domcontentloaded")
            page.get_by_text("couldn't load", exact=False).first.wait_for(timeout=20_000)
        finally:
            browser.close()
