"""Invented report bytes only; old archives cannot be overwritten or financially waived."""

import copy
import gzip
import json
from types import SimpleNamespace

import pytest

from tests.constraints.test_fractional_order_fees import context, filled, model
from tests.test_private_archive_migration import FakeGitHub
from tools import rotation_stress_tenths_compare as f


def test_parent_report_attributes_every_component_and_rejects_accounting_mismatch():
    fees = model()
    for execution, quantity in (("whole", 2), ("tail", 0.5)):
        filled(fees, execution, quantity, 200, context("invented-parent", side="SELL"))
    broker = SimpleNamespace(
        fee_records=fees.records,
        fills=[SimpleNamespace(commission=r.fees.total_fees) for r in fees.records],
    )
    report = f.order_fee_audit(SimpleNamespace(broker=broker))
    assert len(report["orders"]) == 1
    order = report["orders"][0]
    assert order["fill_count"] == 2 and order["quantity"] == f.Decimal("2.5")
    assert order["components_usd"]["broker_commission"] == f.Decimal("0.35")
    assert order["components_usd"]["sec_fees"] > 0
    assert order["components_usd"]["clearing_fees"] > 0
    assert report["actual_account_reconciliation"] is False
    broker.fills[0].commission += 0.01
    with pytest.raises(ValueError, match="PARENT_FEE_REPORT_MISMATCH"):
        f.order_fee_audit(SimpleNamespace(broker=broker))


def prior_payload():
    logical = {
        "comparison.json": b'{"input_fingerprint":"invented"}',
        "results.json": b'{"cash":"1.00"}',
    }
    payload, index = {}, {}
    for name, raw in logical.items():
        body = gzip.compress(raw, mtime=0)
        path = f.PRIOR_PREFIX + name + ".gz.part-00.bin"
        payload[path] = body
        index[name] = {
            "parts": [path],
            "uncompressed_sha256": f.c.m.sha256(raw),
            "compressed_sha256": f.c.m.sha256(body),
        }
    payload[f.PRIOR_PREFIX + "manifest.json"] = f.u.canonical(
        {
            "source_commit": f.PRIOR_SOURCE,
            "logical_files": index,
            "files": {k: f.c.m.sha256(v) for k, v in payload.items()},
        }
    )
    return logical, payload


def test_prior_preview_is_pinned_bounded_and_read_only(monkeypatch):
    archive = FakeGitHub(f.c.m.DESTINATION)
    logical, payload = prior_payload()
    f.c.b.create(archive, f.PRIOR_REF, payload, f.c.m.BOOTSTRAP)
    pin = f.c.m.sha256(f.u.canonical({k: f.c.m.sha256(v) for k, v in sorted(payload.items())}))
    monkeypatch.setattr(f, "PRIOR_BUNDLE", pin)
    archive.events.clear()
    assert f.load_prior(archive)[1] == logical
    assert all(method == "GET" for method, _ in archive.events)
    monkeypatch.setattr(f, "PRIOR_BUNDLE", "0" * 64)
    with pytest.raises(ValueError, match="PRIOR_BUNDLE_CHANGED"):
        f.load_prior(archive)


def control():
    return {
        "cash": "100.000000000001",
        "tenths": {
            name: {"engine": {"assumptions": {"fractional_fee_policy": "published_per_execution"}}}
            for name in ("rotation", "funded_SPY")
        },
    }


def test_only_the_two_declared_policy_labels_can_differ(monkeypatch):
    current = control()
    old = copy.deepcopy(current)
    for node in old["tenths"].values():
        node["engine"]["assumptions"].pop("fractional_fee_policy")
    normalized = b"invented exact normalized input"
    manifest = {"input_fingerprint": "invented"}
    saved = {
        "comparison.json": f.u.canonical(manifest | {"input_sha256": f.c.m.sha256(normalized)}),
        "results.json": f.u.canonical(old),
    }
    monkeypatch.setattr(f, "load_prior", lambda _: (manifest, saved))
    inputs = SimpleNamespace(fingerprint="invented")
    assert f.prior_check(None, inputs, normalized, current)["legacy_controls_exactly_reproduced"]
    current["cash"] = "100.000000000002"
    with pytest.raises(f.PriorControlMismatch) as error:
        f.prior_check(None, inputs, normalized, current)
    assert "100.000000000002" not in json.dumps(error.value.diagnostic)
    assert saved["results.json"] == f.u.canonical(old)
    with pytest.raises(ValueError, match="PRIOR_INPUT_BYTES_CHANGED"):
        f.prior_check(None, inputs, normalized + b" ", current)


def test_distinct_new_ref_fresh_every_byte_and_no_overwrite():
    assert f.REPORT_REF != f.PRIOR_REF and f.PREFIX != f.PRIOR_PREFIX
    archive = FakeGitHub(f.c.m.DESTINATION)
    payload = {
        f.PREFIX + "manifest.json": b"{}",
        f.PREFIX + "comparison.json.gz.part-00.bin": b"invented",
    }
    with pytest.raises(ValueError, match="PRIVATE_REPORT_MISSING"):
        f.retain_report(archive, payload, verify_only=True)
    assert not archive.refs
    first = f.retain_report(archive, payload)
    saved = copy.deepcopy(archive.refs)
    archive.events.clear()
    assert f.retain_report(archive, payload, verify_only=True) == first
    assert all(method == "GET" for method, _ in archive.events)
    with pytest.raises(ValueError, match="PRIVATE_REPORT_BYTE_MISMATCH"):
        f.retain_report(
            archive, payload | {f.PREFIX + "comparison.json.gz.part-00.bin": b"changed"}
        )
    assert archive.refs == saved
    assert f.PRIOR_REF not in archive.refs


@pytest.mark.parametrize("change", ["core", "test", "workflow"])
def test_guard_rejects_changed_sealed_sources_original_tests_and_wrong_workflow(
    monkeypatch, change
):
    source = "b" * 40
    for key, value in {
        "GITHUB_REPOSITORY": f.c.m.CODE,
        "GITHUB_ACTOR": "dczhhh",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_WORKFLOW_REF": f.c.m.CODE + "/" + f.WORKFLOW + "@refs/heads/main",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_OS": "Linux",
        "ACTIONS_STEP_DEBUG": "",
        "ACTIONS_RUNNER_DEBUG": "",
    }.items():
        monkeypatch.setenv(key, value)

    def output(args, **kwargs):
        if "rev-parse" in args:
            return source + "\n"
        if "--name-only" in args:
            return "src/quant_constraints/models.py\n" if change == "core" else ""
        return (
            "M\ttests/constraints/test_verification_orchestration.py\n" if change == "test" else ""
        )

    monkeypatch.setattr(f.subprocess, "check_output", output)
    if change == "workflow":
        monkeypatch.setenv("GITHUB_WORKFLOW_REF", "wrong")
    with pytest.raises(ValueError, match="TENTHS_FEE_"):
        f.guarded(source)


def test_cli_failure_never_echoes_private_error_text(monkeypatch, capsys):
    monkeypatch.setattr(f.sys, "argv", ["fees", "run", "b" * 40])
    monkeypatch.setattr(f, "guarded", lambda _: None)
    monkeypatch.setenv("ARCHIVE_WRITE_TOKEN", "invented credential not used")

    def fail(*args, **kwargs):
        raise ValueError("invented private account price and quantity")

    monkeypatch.setattr(f, "build", fail)
    assert f.main() == 1
    output = capsys.readouterr().out
    assert "private account" not in output
    assert json.loads(output)["performance_admission"] is False
