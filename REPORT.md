# Resilience report: booking platform under failure

| | |
|---|---|
| Version | 1.0 (initial assessment) |
| Date | 27 November 2026 |
| Prepared by | Kurtis H |
| Targets | Booking API ([api-performance-lab](https://github.com/Kurtis-H/api-performance-lab)) and web app ([booking-web](https://github.com/Kurtis-H/booking-web)) |
| Approach | Fault injection with [Toxiproxy](https://github.com/Shopify/toxiproxy) and database locks, against the running stack in CI |

## 1. Executive summary

The booking platform was run with its dependencies failing on purpose: the database refusing connections, answering slowly, dropping connections mid-request and staying locked, and the API going silent for the web app. **4 findings were raised: 1 High, 3 Medium and 0 Low.** Each is an automated experiment, so it can be rerun after a fix.

The most serious issue is that the API has no time limits. When another session locks a table, as a long migration would, requests that need it wait until the lock is released. Each one holds a worker thread and a database connection, so a lock that lasts long enough stalls the whole service (RL-01). The web app has the same gap: if the API stops answering, pages wait forever (RL-04).

When the database goes away entirely, the API fails fast but badly: a plain-text 500 with no hint to retry (RL-02), sometimes no response at all, and a health check that still says everything is fine (RL-03).

The foundations are good. The API recovers on its own as soon as the database is back, with no restart. Moderate database latency slows it down but nothing fails. Most importantly, orders are all or nothing: when database connections were cut while orders were in flight, no seat was sold without an order and every confirmed order was in the database.

**Recommended priorities:**

1. Give every database call a time limit, so a lock or a slow query fails fast instead of piling up (RL-01).
2. Turn database failures into a clear `503` with `Retry-After`, and add a readiness check (RL-02, RL-03).
3. Give the web app's requests a time limit, and tell buyers when an order's outcome is unknown (RL-04).

## 2. Scope

| In scope | Version tested |
|---|---|
| Booking API, with its PostgreSQL database behind a Toxiproxy proxy | `main` of api-performance-lab on the test date |
| booking-web's production build, with the API behind a Toxiproxy proxy | `main` of booking-web on the test date |

**Out of scope:** the Android app, hosting and network infrastructure, and load-driven failures (covered by api-performance-lab's load tests).

**Rules of engagement:** every experiment runs on a disposable stack in a GitHub Actions runner. Toxiproxy listens on the runner's loopback interface only. Faults are removed after each experiment, and the next one starts only once the API is serving again.

## 3. Method

Each experiment states a fault, what a resilient system would do under it, and then injects the fault and measures what actually happens.

| Fault | How it's injected |
|---|---|
| Database outage | Toxiproxy's `db` proxy disabled: new and open connections are refused |
| Database latency | Toxiproxy `latency` toxic, 50 ms on every response from the database |
| Dropped database connections | Toxiproxy `reset_peer` toxic while orders are in flight |
| Locked table | A second session takes `LOCK TABLE seats IN ACCESS EXCLUSIVE MODE`, as a migration would |
| API outage | Toxiproxy's `api` proxy disabled |
| Silent API | Toxiproxy `timeout` toxic on requests, with no timeout: connections stay open, the API never sees the request, no response arrives |

Severity is rated on what a real deployment would suffer: High when one fault can take down the whole service, Medium when users or operators get the wrong signal, Low for cosmetic issues.

## 4. Findings summary

| ID | Finding | Severity | Component | Status |
|---|---|---|---|---|
| RL-01 | A locked table stalls requests until the lock is released | High | booking API | open |
| RL-02 | A database outage returns a bare 500 | Medium | booking API | open |
| RL-03 | Nothing reports that the database is down | Medium | booking API | open |
| RL-04 | The web app waits forever on an API that doesn't answer | Medium | booking-web | open |

## 5. Findings

### RL-01: A locked table stalls requests until the lock is released

**High** · booking API

**Fault.** Another session holds an exclusive lock on `seats`.

**Expected.** Requests that need the table fail fast with 503, within a few seconds.

**Observed.** `GET /events/{id}/seats` got no answer within 20 seconds. It completed only after the lock was released.

**Impact.** Each stalled request holds one of the API's worker threads and one of its database connections. A lock held through a busy minute, from a migration, a backup or a stuck transaction, uses up the pool, and then every endpoint stops answering, including ones that never touch the locked table.

**Remediation.** Set `lock_timeout` and `statement_timeout` on the API's database connections (a few seconds, well above any normal query), and answer `503` when they fire (see RL-02).

### RL-02: A database outage returns a bare 500

**Medium** · booking API

**Fault.** The database stops accepting connections.

**Expected.** 503 in the API's usual JSON error format, with Retry-After, so clients know to retry.

**Observed.** `500 Internal Server Error`, as `text/plain`, with no `Retry-After`. It was fast (about 10 ms), which is good. While database connections were being reset during orders, some requests got no HTTP response at all: the API closed the connection.

**Impact.** A client can't tell an outage from a bug, has no hint when to retry, and doesn't get the API's usual JSON error (`{"detail": ...}`). Monitoring counts it as a server bug. A dropped connection is worse: the client doesn't know whether its order went through.

**Remediation.** Handle database connection errors in one place: answer `503` in the usual JSON error format with `Retry-After`, and count them in a metric.

### RL-03: Nothing reports that the database is down

**Medium** · booking API

**Fault.** The database stops accepting connections.

**Expected.** A readiness check answers 503, so a load balancer stops sending traffic.

**Observed.** `/health` answered `200` throughout the outage, and there is no readiness endpoint (`/ready` is `404`).

**Impact.** A load balancer or orchestrator keeps sending every request to an instance that can't serve any of them, and dashboards built on the health check stay green during an outage.

**Remediation.** Keep `/health` as liveness (the process is up). Add `/ready`, which checks the database with a short timeout and answers `503` when it can't reach it.

### RL-04: The web app waits forever on an API that doesn't answer

**Medium** · booking-web

**Fault.** The API accepts connections but never sends a response.

**Expected.** The page gives up after a time limit and shows an error.

**Observed.** After 30 seconds the events page was still loading, with no error and no way forward except reloading.

**Impact.** Buyers see a spinner that never ends. For an order, it's worse: the buyer can't tell whether it went through, and may buy twice or give up.

**Remediation.** Give every API request a time limit. On a timeout, show the usual load error, and for an order, say its outcome is unknown and point to "My orders" before trying again.

## 6. What held up

These experiments passed and are kept as tests in [`tests/test_controls.py`](tests/test_controls.py):

- **Recovery without a restart.** After a database outage, the API served again within a second of the database coming back. The connection pool checks connections before use and replaces dead ones.
- **Latency degrades gracefully.** With 50 ms added to every database response, `GET /events` slowed from a few milliseconds to about a quarter of a second (each request makes several round trips), and none failed.
- **Orders are all or nothing.** With database connections reset while 20 orders were in flight, every seat marked sold belonged to an order, no order was left without seats, and every order the API confirmed was in the database.
- **The web app reports an API outage.** With the API unreachable, the events page shows its "couldn't load" error.

## Appendix A: Reproducing the experiments

CI starts Postgres, Toxiproxy, the API (connected through the `db` proxy) and the web app's production build (connected through the `api` proxy), all from `main`, then runs `tests/`. A finding marked open or accepted must reproduce, and one marked fixed must not. See the [README](README.md) to run it.
