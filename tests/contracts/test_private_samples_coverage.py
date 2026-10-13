"""New admission/batch gate is additive and private execution is source-pinned."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from quant_constraints.research.data.sample_contracts import sample_slices
from validation.check_private_samples_coverage import MODULES, coverage_failures


def payload():
    return {
        "meta": {"branch_coverage": True},
        "files": {
            m: {"summary": {"percent_statements_covered": 94.0, "percent_branches_covered": 85.0}}
            for m in MODULES
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
@pytest.mark.parametrize("module", MODULES)
def test_invalid_or_low_module_evidence_cannot_skip_gate(metric, value, module):
    data = payload()
    data["files"][module]["summary"][f"percent_{metric}_covered"] = value
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
def test_missing_measurements_fail_closed(data):
    assert coverage_failures(data)


def test_all_nine_ci_gates_remain_and_public_ci_cannot_capture():
    root = Path(__file__).resolve().parents[2]
    text = (root / ".github/workflows/ci.yml").read_text()
    ci = yaml.safe_load(text)
    assert "issue-5f2-private-intake" in ci[True]["pull_request"]["branches"]
    commands = "\n".join(s.get("run", "") for s in ci["jobs"]["coverage"]["steps"])
    for name in (
        "coverage",
        "research_raw_coverage",
        "research_pit_coverage",
        "research_entry_coverage",
        "research_permission_coverage",
        "research_demand_coverage",
        "private_capture_coverage",
        "private_ingest_coverage",
        "private_samples_coverage",
    ):
        assert "check_" + name + ".py" in commands
    assert "coverage" in ci["jobs"]["build"]["needs"]
    assert "b5ecfd8527fe8eb6398a09b7f74e802e4324789c" in text
    assert "secrets.massive" not in text and "private_sample_batch capture" not in text
    lint_commands = "\n".join(s.get("run", "") for s in ci["jobs"]["lint"]["steps"])
    assert "actionlint/releases/download/v1.7.12/" in lint_commands
    assert "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8" in lint_commands
    assert "sha256sum --check --strict" in lint_commands
    assert "-pyflakes='' .github/workflows/private-samples.yml" in lint_commands


def test_only_owner_pinned_serial_private_jobs_release_provider_key():
    root = Path(__file__).resolve().parents[2]
    text = (root / ".github/workflows/private-samples.yml").read_text()
    workflow = yaml.safe_load(text)
    assert set(workflow[True]) == {"push", "workflow_dispatch"}
    assert workflow[True]["push"]["branches"] == ["research-execution/samples-v1"]
    assert workflow[True]["workflow_dispatch"] is None
    guard = workflow["jobs"]["reviewed-code"]
    for fragment in (
        "github.actor == 'dczhhh'",
        "github.sha == vars.MASSIVE_SAMPLES_REVIEWED_SHA",
        "vars.MASSIVE_SAMPLES_ENABLED == 'true'",
    ):
        assert fragment in guard["if"]
    assert ".private == true" in guard["steps"][0]["run"]
    capture = workflow["jobs"]["capture-and-publish"]
    assert capture["strategy"]["max-parallel"] == 1 and capture["strategy"]["fail-fast"] is False
    assert capture["strategy"]["matrix"]["slice"] == [s.capture_id for s in sample_slices()]
    assert workflow["concurrency"]["cancel-in-progress"] is False
    audit = workflow["jobs"]["structural-audit"]
    assert "always()" in audit["if"]
    for job in (capture, audit):
        assert job["runs-on"] == "ubuntu-latest" and job["timeout-minutes"] == 15
        # GitHub rejects runner context in job-level env; it exists only in steps.
        assert "RUNNER_ENVIRONMENT" not in job["env"]
        assert job["steps"][0]["with"]["persist-credentials"] is False
        assert job["steps"][0]["with"]["ref"] == "${{ needs.reviewed-code.outputs.commit }}"
        for step in job["steps"]:
            if "Prepare" in step.get("name", "") or "private_sample_batch" in step.get("run", ""):
                assert step["env"]["RUNNER_ENVIRONMENT"] == "${{ runner.environment }}"
            if "MASSIVE_API_KEY" in step.get("env", {}):
                assert job is capture and step["run"].endswith('capture "$SAMPLE_ID"')
                assert step["env"]["MASSIVE_API_KEY"] == "${{ secrets.massive }}"
    assert text.count("secrets.massive") == 1
    assert "upload-artifact" not in text and "inputs." not in text and "schedule:" not in text
