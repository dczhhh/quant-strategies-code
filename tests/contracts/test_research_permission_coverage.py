"""5F-0 coverage evidence cannot omit branches/modules or remove prior gates."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from validation.check_research_permission_coverage import MODULES, coverage_failures


def payload():
    return {
        "meta": {"branch_coverage": True},
        "files": {
            module: {
                "summary": {"percent_statements_covered": 94.0, "percent_branches_covered": 85.0}
            }
            for module in MODULES
        },
    }


def test_exact_threshold_evidence_passes_without_mutation():
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
def test_invalid_or_below_threshold_evidence_blocks(metric, value):
    data = payload()
    data["files"][MODULES[0]]["summary"][f"percent_{metric}_covered"] = value
    assert len(coverage_failures(data)) == 1


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"meta": None},
        {"meta": {"branch_coverage": False}},
        {"meta": {"branch_coverage": True}, "files": None},
        {"meta": {"branch_coverage": True}, "files": {}},
        {"meta": {"branch_coverage": True}, "files": {MODULES[0]: {"summary": None}}},
    ],
)
def test_missing_measurements_cannot_skip_coverage_gate(data):
    assert coverage_failures(data)


def test_stacked_ci_preserves_all_gates_and_offline_scope():
    root = Path(__file__).resolve().parents[2]
    ci = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())
    assert "issue-5e4-entry-e2e" in ci[True]["pull_request"]["branches"]
    commands = "\n".join(step.get("run", "") for step in ci["jobs"]["coverage"]["steps"])
    for checker in (
        "check_coverage.py",
        "check_research_raw_coverage.py",
        "check_research_pit_coverage.py",
        "check_research_entry_coverage.py",
        "check_research_permission_coverage.py",
    ):
        assert checker in commands
    assert "coverage" in ci["jobs"]["build"]["needs"]
    assert (
        "b5ecfd8527fe8eb6398a09b7f74e802e4324789c"
        in (root / ".github/workflows/ci.yml").read_text()
    )
