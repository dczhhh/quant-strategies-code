"""5E thresholds are additional blocking evidence, including the stacked PR trigger."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from validation.check_research_entry_coverage import MODULES, coverage_failures


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


def test_thresholds_are_exact_and_evidence_unchanged():
    data = payload()
    before = deepcopy(data)
    assert coverage_failures(data) == [] and data == before


@pytest.mark.parametrize("module", MODULES)
@pytest.mark.parametrize("metric,minimum", [("statements", 94), ("branches", 85)])
def test_each_module_below_threshold_fails(module, metric, minimum):
    data = payload()
    data["files"][module]["summary"][f"percent_{metric}_covered"] = minimum - 0.01
    assert len(coverage_failures(data)) == 1


@pytest.mark.parametrize("value", [None, True, float("nan"), float("inf"), -1, 101])
def test_invalid_measurements_are_rejected(value):
    data = payload()
    data["files"][MODULES[0]]["summary"]["percent_branches_covered"] = value
    assert len(coverage_failures(data)) == 1


def test_missing_modules_or_branch_collection_cannot_skip_gate():
    for data in (
        {},
        {"meta": None},
        {"meta": {"branch_coverage": False}},
        {"meta": {"branch_coverage": True}, "files": None},
        {"meta": {"branch_coverage": True}, "files": {}},
    ):
        assert coverage_failures(data)


def test_stacked_pr_runs_full_ci_and_retains_all_existing_coverage_gates():
    root = Path(__file__).resolve().parents[2]
    ci = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())
    ecosystem = yaml.safe_load((root / ".github/workflows/ecosystem.yml").read_text())
    # YAML 1.1 parses the workflow's unquoted `on` as True.
    assert "issue-5d-pit-core" in ci[True]["pull_request"]["branches"]
    assert "issue-5e1-verification-contracts" in ci[True]["pull_request"]["branches"]
    assert "issue-5e2-verification-orchestration" in ci[True]["pull_request"]["branches"]
    assert "issue-5e3-research-entry" in ci[True]["pull_request"]["branches"]
    assert "pull_request" in ecosystem[True] and ecosystem[True]["pull_request"] is None
    commands = "\n".join(step.get("run", "") for step in ci["jobs"]["coverage"]["steps"])
    for checker in (
        "check_coverage.py",
        "check_research_raw_coverage.py",
        "check_research_pit_coverage.py",
        "check_research_entry_coverage.py",
    ):
        assert checker in commands
    assert "coverage" in ci["jobs"]["build"]["needs"]
