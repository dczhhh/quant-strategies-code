"""The 5D gate is additive and rejects missing/malformed branch evidence."""

from copy import deepcopy

import pytest

from validation.check_research_pit_coverage import MODULE, MODULES, coverage_failures


def evidence():
    return {
        "meta": {"branch_coverage": True},
        "files": {
            module: {
                "summary": {
                    "percent_statements_covered": 94.0,
                    "percent_branches_covered": 85.0,
                }
            }
            for module in MODULES
        },
    }


def test_thresholds_and_no_mutation():
    payload = evidence()
    before = deepcopy(payload)
    assert coverage_failures(payload) == [] and payload == before


@pytest.mark.parametrize("kind,minimum", [("statements", 94.0), ("branches", 85.0)])
def test_gate_not_relaxed(kind, minimum):
    payload = evidence()
    payload["files"][MODULE]["summary"][f"percent_{kind}_covered"] = minimum - 0.01
    assert len(coverage_failures(payload)) == 1


@pytest.mark.parametrize("bad", [None, True, float("nan"), float("inf"), -1, 101])
def test_invalid_percentage_rejected(bad):
    payload = evidence()
    payload["files"][MODULE]["summary"]["percent_branches_covered"] = bad
    assert len(coverage_failures(payload)) == 1


def test_missing_module_or_branch_collection_rejected():
    assert coverage_failures({"meta": {"branch_coverage": True}, "files": {}})
    assert coverage_failures({"meta": {"branch_coverage": False}, "files": {}})


def test_supply_module_required_and_same_thresholds():
    payload = evidence()
    payload["files"].pop(MODULES[1])
    assert len(coverage_failures(payload)) == 1
    payload = evidence()
    payload["files"][MODULES[1]]["summary"]["percent_statements_covered"] = 93.99
    assert len(coverage_failures(payload)) == 1
