"""The findings register: one entry per weakness found by fault injection.

Each finding is an experiment: a fault (from Toxiproxy, or a database lock)
and what the system should do under it. `status` drives tests/test_findings.py,
the same way as in booking-pentest:
  open       the weakness reproduces: its test expects the bad behavior
  accepted   the risk is accepted (with a reason): still reproduces
  fixed      remediated: its test expects the good behavior (the retest)

Experiments where the system already behaves well are in tests/test_controls.py.
REPORT.md is checked against this file by tests/test_report.py.
"""

from __future__ import annotations

from dataclasses import dataclass

SEVERITIES = ("High", "Medium", "Low")


@dataclass(frozen=True)
class Finding:
    id: str
    title: str
    severity: str
    component: str
    fault: str  # the fault injected
    expected: str  # what a resilient system does under it
    status: str  # open | accepted | fixed
    fixed_in: str = ""
    accepted_because: str = ""

    @property
    def reproduced(self) -> bool:
        return self.status in ("open", "accepted")


FINDINGS = [
    Finding(
        id="RL-01",
        title="A locked table stalls requests until the lock is released",
        severity="High",
        component="booking API",
        fault="Another session holds an exclusive lock on `seats`",
        expected="Requests that need the table fail fast with 503, within a few seconds",
        status="open",
    ),
    Finding(
        id="RL-02",
        title="A database outage returns a bare 500",
        severity="Medium",
        component="booking API",
        fault="The database stops accepting connections",
        expected="503 with a problem+json body and Retry-After, so clients know to retry",
        status="open",
    ),
    Finding(
        id="RL-03",
        title="Nothing reports that the database is down",
        severity="Medium",
        component="booking API",
        fault="The database stops accepting connections",
        expected="A readiness check answers 503, so a load balancer stops sending traffic",
        status="open",
    ),
    Finding(
        id="RL-04",
        title="The web app waits forever on an API that doesn't answer",
        severity="Medium",
        component="booking-web",
        fault="The API accepts connections but never sends a response",
        expected="The page gives up after a time limit and shows an error",
        status="open",
    ),
]

BY_ID = {f.id: f for f in FINDINGS}
