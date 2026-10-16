"""Invented full stress sources only; canonical engines and fresh private readback."""

import copy
import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from tests.test_private_archive_migration import FakeGitHub
from tests.test_rotation_stress_inputs import stress_sources  # noqa: F401
from tools import rotation_stress_replay as r

SOURCE = "b" * 40


@pytest.fixture
def replay(request):
    docs, _, _, _ = request.getfixturevalue("stress_sources")
    archive = FakeGitHub(r.c.m.DESTINATION)

    def inputs(factory, source, audit):
        return r.x.build_inputs(
            factory,
            source,
            audit,
            docs=(docs, {"status": "UNKNOWN_REQUEST_OUTCOME_RETAINED"}),
            published_at=datetime(2026, 10, 16, tzinfo=UTC),
        )

    return archive, inputs


def test_six_actual_engines_and_fresh_readonly_report_are_byte_identical(replay):
    archive, builder = replay
    progress = {"engine_invoked": False}
    first = r.execute(lambda: archive, SOURCE, input_builder=builder, progress=progress)
    assert progress["engine_invoked"]
    assert first["execution_sessions"] == 143
    assert first["share_quantum"] == "1"
    assert first["decision_oracle"]["status"].endswith("ORACLE_PASS")
    assert first["performance_admission"] is False
    assert first["classification_2024_real_source_verified"] is False
    assert first["regression"]["status"] == "INCONCLUSIVE"
    assert len(first["tax_sensitivity_not_parameter_optimization"]) == 2
    saved = copy.deepcopy(archive.refs)
    archive.events.clear()
    second = r.execute(lambda: archive, SOURCE, input_builder=builder, verify_only=True)
    assert first == second and saved == archive.refs
    assert all(method == "GET" for method, _ in archive.events)
    assert all(len(body) <= r.c.m.FILE_LIMIT for body in archive.blobs.values())
    assert json.loads(json.dumps(first))["full_real_scenarios_accepted"] == 0


def test_failed_source_does_not_run_engine_or_create_report(replay):
    archive, _ = replay
    progress = {"engine_invoked": False}

    def missing(*_args):
        raise ValueError("invented missing original source")

    with pytest.raises(ValueError):
        r.execute(lambda: archive, SOURCE, input_builder=missing, progress=progress)
    assert progress["engine_invoked"] is False
    assert not archive.refs


def test_failed_independent_decision_check_cannot_archive_pass(replay, monkeypatch):
    archive, builder = replay

    def corrupt(_):
        raise r.o.DecisionOracleMismatch("invented mismatch")

    monkeypatch.setattr(r.o.DecisionOracle, "finish", corrupt)
    with pytest.raises(r.o.DecisionOracleMismatch):
        r.execute(lambda: archive, SOURCE, input_builder=builder)
    assert not archive.refs


def test_compressed_parts_are_bounded_and_lossless(monkeypatch):
    monkeypatch.setattr(r.c.m, "FILE_LIMIT", 16)
    raw = bytes(range(50))
    payload, index = r.compressed_payload({"invented.json": raw})
    assert len(payload) > 1 and all(len(body) <= 16 for body in payload.values())
    packed = b"".join(payload[path] for path in index["invented.json"]["parts"])
    assert gzip.decompress(packed) == raw
    with pytest.raises(ValueError, match="TOO_LARGE"):
        r.compressed_payload({"invented.json": bytes(range(256))})


def test_failure_output_omits_private_messages(monkeypatch, capsys):
    monkeypatch.setattr(r.sys, "argv", ["replay", "run", SOURCE])
    monkeypatch.setattr(r.c, "guarded", lambda _: None)

    def denied(*_args, **_kwargs):
        raise r.o.DecisionOracleMismatch("invented licensed price and secret")

    monkeypatch.setattr(r, "execute", denied)
    assert r.main() == 1
    output = capsys.readouterr().out
    assert "invented licensed" not in output
    assert json.loads(output)["reason"] == "DECISION_ORACLE_MISMATCH"


def test_launcher_has_no_vendor_secret_and_pins_reviewed_source():
    path = Path(__file__).resolve().parents[1] / "docs/examples/rotation-stress-replay.yml"
    content = path.read_text()
    config = yaml.safe_load(content)
    assert config["env"]["SOURCE_SHA"] == "__REVIEWED_SOURCE_SHA__"
    assert "MASSIVE" not in content
    assert config["jobs"]["verify"]["needs"] == "replay"
    assert config["concurrency"]["cancel-in-progress"] is False
    assert "verify_only" not in content
    assert 'rotation_stress_replay.py verify "$SOURCE_SHA"' in content
