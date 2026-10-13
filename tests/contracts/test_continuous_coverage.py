"""Coverage, workflow, preparation CLI and second resource are additive contracts."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from validation import check_continuous_preparation as cli
from validation.check_continuous_coverage import MODULES, coverage_failures

ROOT = Path(__file__).resolve().parents[2]


def evidence():
    return {
        "meta": {"branch_coverage": True},
        "files": {
            m: {"summary": {"percent_statements_covered": 94.0, "percent_branches_covered": 85.0}}
            for m in MODULES
        },
    }


def test_gate_is_read_only_and_thresholds_pass():
    data = evidence()
    before = deepcopy(data)
    assert coverage_failures(data) == [] and data == before


@pytest.mark.parametrize("module", MODULES)
@pytest.mark.parametrize(
    "metric,value",
    [
        ("statements", 93.99),
        ("branches", 84.99),
        ("statements", True),
        ("branches", None),
        ("branches", float("nan")),
        ("statements", 101),
        ("statements", -1),
    ],
)
def test_missing_or_low_evidence_cannot_bypass_tenth_gate(module, metric, value):
    data = evidence()
    data["files"][module]["summary"]["percent_" + metric + "_covered"] = value
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
def test_missing_coverage_fails_closed(data):
    assert coverage_failures(data)


def test_public_ci_does_not_acquire_and_original_nine_gates_remain():
    text = (ROOT / ".github/workflows/ci.yml").read_text()
    ci = yaml.load(text, Loader=yaml.BaseLoader)
    assert "issue-5f3-private-samples" in ci["on"]["pull_request"]["branches"]
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
        "continuous_coverage",
    ):
        assert "check_" + name + ".py" in commands
    assert "check_continuous_preparation.py" in commands
    assert "secrets.massive" not in text and "continuous_materials capture" not in text
    assert "continuous-materials.yml" in text and "actionlint/releases/download/v1.7.12/" in text
    release = (ROOT / "validation/release_checks.txt").read_text()
    assert "validation/check_continuous_preparation.py" in release
    assert "validation/check_continuous_coverage.py" in release


def test_secret_is_one_step_and_scope_never_accepts_user_inputs():
    path = ROOT / ".github/workflows/continuous-materials.yml"
    text = path.read_text()
    wf = yaml.load(text, Loader=yaml.BaseLoader)
    assert wf["on"]["push"]["branches"] == ["research-execution/continuous-materials-v1"]
    assert wf["on"]["workflow_dispatch"] == ""
    assert wf["permissions"]["contents"] == "read"
    job = wf["jobs"]["reference-only"]
    for condition in (
        "github.actor == 'dczhhh'",
        "vars.MASSIVE_CONTINUOUS_ENABLED == 'true'",
        "github.sha == vars.MASSIVE_CONTINUOUS_REVIEWED_SHA",
    ):
        assert condition in job["if"]
    assert "RUNNER_ENVIRONMENT" not in job["env"]
    credential_steps = [s for s in job["steps"] if "secrets.massive" in str(s)]
    assert len(credential_steps) == 1 and credential_steps[0]["run"].endswith(" capture")
    assert "persist-credentials" in text and "persist-credentials: false" in text
    assert "upload-artifact" not in text and "workflow_call" not in text and "inputs:" not in text
    assert text.count("runner.environment") == 4


def test_fixed_metadata_cli_reports_only_preparation(capsys):
    assert cli.main(["--synthetic"]) == 0
    text = capsys.readouterr().out
    assert '"status":"PREPARATION_ONLY"' in text and '"market_requests":0' in text
    assert '"may_acquire_aggregates":false' in text and '"may_build_engine":false' in text


def test_cli_redacts_unreadable_input_and_never_grants_execution(monkeypatch, capsys):
    def stopped(*_):
        raise OSError("PRIVATE_PATH_DO_NOT_PRINT")

    monkeypatch.setattr(cli.Path, "open", stopped)
    assert cli.main(["--synthetic"]) == 1
    text = capsys.readouterr().out
    assert "PRIVATE_PATH" not in text and "CONTINUOUS_INPUT_STOPPED" in text
