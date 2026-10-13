"""The 5C gate adds thresholds; it never edits or replaces the frozen upstream gate."""

from copy import deepcopy

import pytest

from validation.check_research_raw_coverage import MODULE, coverage_failures


def evidence():
    return {
        "meta": {"branch_coverage": True},
        "files": {
            MODULE: {
                "summary": {"percent_statements_covered": 94.0, "percent_branches_covered": 85.0}
            }
        },
    }


def test_research_gate_thresholds_and_no_mutation():
    payload = evidence()
    before = deepcopy(payload)
    assert coverage_failures(payload) == []
    assert payload == before


@pytest.mark.parametrize("kind,minimum", [("statements", 94.0), ("branches", 85.0)])
def test_threshold_is_not_silently_relaxed(kind, minimum):
    payload = evidence()
    payload["files"][MODULE]["summary"][f"percent_{kind}_covered"] = minimum - 0.01
    assert len(coverage_failures(payload)) == 1


@pytest.mark.parametrize("bad", [None, True, float("nan"), float("inf"), -1, 101])
def test_missing_nonfinite_and_invalid_percentage_rejected(bad):
    payload = evidence()
    payload["files"][MODULE]["summary"]["percent_branches_covered"] = bad
    assert len(coverage_failures(payload)) == 1


def test_module_missing_or_branch_collection_disabled():
    assert coverage_failures({"meta": {"branch_coverage": True}, "files": {}})
    assert coverage_failures({"meta": {"branch_coverage": False}, "files": {}})
