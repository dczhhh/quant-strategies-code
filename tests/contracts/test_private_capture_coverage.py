"""New operator evidence cannot omit measurements or weaken the accepted CI gates."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from validation.check_private_capture_coverage import MODULE, coverage_failures


def payload():
    return {
        "meta": {"branch_coverage": True},
        "files": {
            MODULE: {
                "summary": {"percent_statements_covered": 94.0, "percent_branches_covered": 85.0}
            }
        },
    }


def test_threshold_pass_is_read_only():
    data = payload()
    before = deepcopy(data)
    assert coverage_failures(data) == [] and data == before


@pytest.mark.parametrize(
    "metric,value",
    [
        ("statements", 93.99),
        ("branches", 84.99),
        ("statements", True),
        ("branches", None),
        ("branches", float("nan")),
        ("branches", float("inf")),
        ("statements", -1),
        ("statements", 101),
    ],
)
def test_invalid_or_below_threshold_evidence_fails(metric, value):
    data = payload()
    data["files"][MODULE]["summary"][f"percent_{metric}_covered"] = value
    assert len(coverage_failures(data)) == 1


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"meta": None},
        {"meta": {"branch_coverage": False}},
        {"meta": {"branch_coverage": True}, "files": None},
        {"meta": {"branch_coverage": True}, "files": {}},
        {"meta": {"branch_coverage": True}, "files": {MODULE: {"summary": None}}},
    ],
)
def test_missing_operator_evidence_cannot_skip_gate(data):
    assert coverage_failures(data)


def test_new_gate_is_additive_and_public_ci_has_no_capture_or_secret():
    root = Path(__file__).resolve().parents[2]
    text = (root / ".github/workflows/ci.yml").read_text()
    ci = yaml.safe_load(text)
    assert "issue-5f1-slice-preparation" in ci[True]["pull_request"]["branches"]
    commands = "\n".join(step.get("run", "") for step in ci["jobs"]["coverage"]["steps"])
    for checker in (
        "check_coverage.py",
        "check_research_raw_coverage.py",
        "check_research_pit_coverage.py",
        "check_research_entry_coverage.py",
        "check_research_permission_coverage.py",
        "check_research_demand_coverage.py",
        "check_private_capture_coverage.py",
    ):
        assert checker in commands
    assert "coverage" in ci["jobs"]["build"]["needs"]
    assert "b5ecfd8527fe8eb6398a09b7f74e802e4324789c" in text
    assert "secrets.massive" not in text and "--mode capture" not in text
