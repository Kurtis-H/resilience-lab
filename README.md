# resilience-lab

Fault injection experiments against the booking platform: the [booking API](https://github.com/Kurtis-H/api-performance-lab) and its [web app](https://github.com/Kurtis-H/booking-web). What happens when the database goes away, slows down, drops connections or stays locked, or the API stops answering? The [report](REPORT.md) has the answers, and CI checks they're still true.

Faults are injected with [Toxiproxy](https://github.com/Shopify/toxiproxy), a TCP proxy whose behavior can be changed through an API. The API reaches Postgres through one proxy, and the web app reaches the API through another:

```
browser -> booking-web -> [toxiproxy: api] -> booking API -> [toxiproxy: db] -> Postgres
```

## Why it's built this way

- **Every experiment is a hypothesis with a test.** Each finding in [`findings.py`](findings.py) names a fault and what a resilient system would do under it. [`tests/test_findings.py`](tests/test_findings.py) injects the fault and measures what actually happens. A finding marked open must reproduce, and one marked fixed must not: that's the retest.
- **What already works is tested too.** [`tests/test_controls.py`](tests/test_controls.py) keeps the experiments that passed: recovery after an outage, graceful slowdown under latency, and orders staying all or nothing when connections drop mid-request. Each has a control step proving the fault really happened, so a pass means something.
- **Checks invariants, not just status codes.** The dropped-connection experiment checks the database afterwards: every sold seat is in an order, no order is empty, and every order the API confirmed exists.
- **The report can't drift.** [`tests/test_report.py`](tests/test_report.py) checks REPORT.md against the register.
- **The targets are tested at `main`,** on every push and weekly, so a change in either app that breaks one of these properties fails this repo's CI.

## Status

Version 1.1 of the [report](REPORT.md) is the retest: all 4 findings are fixed and verified (RL-01 to RL-03 in the API, RL-04 in booking-web).

The live status of every finding is on the [quality dashboard](https://kurtis-h.github.io/quality-dashboard/), read from this repo's `findings.py`.

## Contained by design

Everything runs in a disposable GitHub Actions runner. Toxiproxy listens on the runner's loopback interface only, and the API, the web app and Postgres are bound to `127.0.0.1` as well. Every experiment removes its faults and waits for the API to serve again before the next one starts.

## Layout

```
findings.py            the findings register: fault, expected behavior, severity, status
scripts/proxies.py     creates the two Toxiproxy proxies
tests/
  conftest.py          the stack's endpoints, a small Toxiproxy client, setup and teardown
  test_findings.py     each finding, reproduced
  test_controls.py     the experiments that already pass, with their controls
  test_report.py       REPORT.md agrees with the register
REPORT.md              the assessment
```

## Run it

CI is the intended way: push, or run the workflow by hand. The job summary shows the evidence for each finding.

To run it locally you need Docker, Postgres, Python 3.13 and Node 22, plus checkouts of api-performance-lab and booking-web. Follow the steps in [`.github/workflows/resilience.yml`](.github/workflows/resilience.yml): start Toxiproxy with `-host=127.0.0.1`, run `scripts/proxies.py`, start the API with its `DATABASE_URL` pointing at port 15432, serve booking-web's build with `API_URL=http://127.0.0.1:18000`, then run `pytest`. The report check runs on its own with no stack: `pytest tests/test_report.py`.

## Related

- [booking-pentest](https://github.com/Kurtis-H/booking-pentest): the security assessment of the same platform, built the same way.
- [api-performance-lab](https://github.com/Kurtis-H/api-performance-lab): load tests and performance gates for the API.
