"""REPORT.md must agree with the findings register."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from findings import FINDINGS, SEVERITIES

REPORT = (Path(__file__).resolve().parents[1] / "REPORT.md").read_text()
ROWS = {
    m["id"]: m
    for m in re.finditer(
        r"^\| (?P<id>RL-\d+) \| (?P<title>[^|]+) \| (?P<severity>\w+) \| (?P<component>[^|]+)"
        r" \| (?P<status>[^|]+) \|$",
        REPORT,
        re.M,
    )
}


def test_every_finding_is_in_the_summary_table_and_nothing_else() -> None:
    assert set(ROWS) == {f.id for f in FINDINGS}


@pytest.mark.parametrize("finding", FINDINGS, ids=lambda f: f.id)
def test_summary_row_matches_the_register(finding) -> None:
    row = ROWS[finding.id]
    assert row["title"].strip() == finding.title
    assert row["severity"] == finding.severity
    assert row["component"].strip() == finding.component
    assert row["status"].strip().startswith(finding.status)


@pytest.mark.parametrize("finding", FINDINGS, ids=lambda f: f.id)
def test_detail_section_matches_the_register(finding) -> None:
    heading = f"### {finding.id}: {finding.title}"
    assert heading in REPORT
    section = REPORT.split(heading, 1)[1].split("\n### ", 1)[0]
    assert f"**{finding.severity}** · {finding.component}" in section
    assert f"**Fault.** {finding.fault}." in section
    assert f"**Expected.** {finding.expected}." in section


def test_findings_are_listed_most_severe_first() -> None:
    ranks = [SEVERITIES.index(f.severity) for f in FINDINGS]
    assert ranks == sorted(ranks)


def test_executive_summary_counts_match() -> None:
    counts = {s: sum(f.severity == s for f in FINDINGS) for s in SEVERITIES}
    expected = (
        f"**{len(FINDINGS)} findings were raised: "
        f"{counts['High']} High, {counts['Medium']} Medium and {counts['Low']} Low."
    )
    assert expected in REPORT


def test_fixed_findings_name_their_fix_and_accepted_ones_their_reason() -> None:
    for f in FINDINGS:
        if f.status == "fixed":
            assert f.fixed_in, f"{f.id} is fixed but doesn't say where"
        if f.status == "accepted":
            assert f.accepted_because, f"{f.id} is accepted without a reason"
