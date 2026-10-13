"""Invented operator fixtures; no actual permit, account, root or provider contact."""

import json
import os
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_research_demand import AS_OF, native_fixture

from quant_constraints.research.data import demand_capture as capture
from quant_constraints.research.data import private_capture_cli as operator
from quant_constraints.research.data.archive_contracts import digest
from quant_constraints.research.data.demand import DemandError


def fixture(tmp_path, monkeypatch):
    root, old, requests, reads = native_fixture(tmp_path, monkeypatch)
    permit = replace(old, plan_sha256=digest(operator.fixed_plan()))
    monkeypatch.setattr(capture, "_ACCEPTED_EXECUTION_REVIEWS", (permit,))
    monkeypatch.setenv(operator.ROOT_ENVIRONMENT, str(root))
    # Simulate reviewed POSIX facts uniformly; native Windows stays unsupported.
    monkeypatch.setattr(
        operator,
        "_root_details",
        lambda path: {
            "root_path_sha256": digest(str(path).encode()),
            "root_identity_sha256": capture.root_identity(path),
        },
    )
    return root, permit, requests, reads


def test_fixed_plan_matches_accepted_file_bytes_without_normalization():
    original = Path(__file__).resolve().parents[2] / "docs/examples/research-demand-aapl-v1.json"
    assert operator.fixed_plan() == original.read_bytes()
    assert digest(operator.fixed_plan()) == operator.PLAN_SHA256


@pytest.mark.parametrize("review", [None, True, "", "A" * 64, "a" * 63, "0" * 64])
def test_missing_registry_rejects_before_any_environment_path_key_or_transport(monkeypatch, review):
    def forbidden(*args, **kwargs):
        raise AssertionError("External access before execution review")

    with monkeypatch.context() as guard:
        guard.setattr(type(os.environ), "get", forbidden)
        guard.setattr(Path, "stat", forbidden)
        guard.setattr(Path, "open", forbidden)
        guard.setattr(capture, "build_opener", forbidden)
        with pytest.raises(DemandError, match="^EXECUTION_SCOPE_REVIEW_PENDING:review$"):
            operator.preflight(review)
    assert capture._ACCEPTED_EXECUTION_REVIEWS == ()


def test_preflight_does_not_inspect_key_and_capture_calls_the_accepted_pipeline(
    tmp_path, monkeypatch, capsys
):
    root, permit, requests, reads = fixture(tmp_path, monkeypatch)
    original = type(os.environ).get

    def guard_environment(self, key, default=None):
        if key == "MASSIVE_API_KEY":
            raise AssertionError("Preflight must not inspect credentials")
        return original(self, key, default)

    with monkeypatch.context() as guard:
        guard.setattr(type(os.environ), "get", guard_environment)
        assert operator.main(["--mode", "preflight", "--review-sha256", permit.review_sha256]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["may_acquire"] is True
    assert report["license_determination"] == "not_performed"
    assert all(report[flag] is False for flag in operator.FLAGS)
    assert requests == []
    assert operator.main(["--mode", "capture", "--review-sha256", permit.review_sha256]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["code"] == "CAPTURE_COMPLETE_UNTRUSTED"
    assert str(root) not in output and "synthetic_fixture_credential" not in output
    assert len(requests) == 2 and all(size <= 524289 for size in reads)
    assert (root / "revisions").is_dir()
    # Same reviewed request re-entry uses the accepted immutable cache, no retry reset.
    operator.acquire(permit.review_sha256)
    assert len(requests) == 2


@pytest.mark.parametrize("problem", ["account", "license", "expiry", "plan"])
def test_invalid_registered_scope_still_stops_before_root_environment(
    tmp_path, monkeypatch, problem
):
    _, permit, _, _ = fixture(tmp_path, monkeypatch)
    changes = {
        "account": {"account_evidence_ref": ""},
        "license": {"license_evidence_ref": ""},
        "expiry": {"valid_until": AS_OF},
        "plan": {"plan_sha256": "f" * 64},
    }[problem]
    monkeypatch.setattr(capture, "_ACCEPTED_EXECUTION_REVIEWS", (replace(permit, **changes),))

    def forbidden(*args, **kwargs):
        raise AssertionError("No root environment access for invalid scope")

    with monkeypatch.context() as guard:
        guard.setattr(type(os.environ), "get", forbidden)
        with pytest.raises(DemandError):
            operator.preflight(permit.review_sha256)


@pytest.mark.parametrize(
    "value",
    [None, "", "\x00", "\n", "x" * 4097],
    ids=["missing", "empty", "nul", "newline", "oversize"],
)
def test_root_environment_is_bounded_without_echoing_input(tmp_path, monkeypatch, value):
    _, permit, _, _ = fixture(tmp_path, monkeypatch)
    original = type(os.environ).get

    def environment(self, key, default=None):
        return value if key == operator.ROOT_ENVIRONMENT else original(self, key, default)

    monkeypatch.setattr(type(os.environ), "get", environment)
    with pytest.raises(DemandError, match="^PRIVATE_ROOT_REQUIRED:root$"):
        operator.preflight(permit.review_sha256)


@pytest.mark.parametrize("problem", ["path", "inode", "revoked", "expired_later", "replaced"])
def test_root_bindings_and_review_rechecked_after_inspection(tmp_path, monkeypatch, problem):
    root, permit, _, _ = fixture(tmp_path, monkeypatch)

    def details(path):
        result = {
            "root_path_sha256": digest(str(path).encode()),
            "root_identity_sha256": capture.root_identity(path),
        }
        if problem == "path":
            result["root_path_sha256"] = "b" * 64
        if problem == "inode":
            result["root_identity_sha256"] = "c" * 64
        if problem == "revoked":
            monkeypatch.setattr(capture, "_ACCEPTED_EXECUTION_REVIEWS", ())
        if problem == "expired_later":
            monkeypatch.setattr(capture, "_now", lambda: permit.valid_until + timedelta(seconds=1))
        if problem == "replaced":
            monkeypatch.setattr(
                capture,
                "_ACCEPTED_EXECUTION_REVIEWS",
                (replace(permit, account_evidence_ref="replacement_fixture"),),
            )
        return result

    monkeypatch.setattr(operator, "_root_details", details)
    with pytest.raises(DemandError):
        operator.preflight(permit.review_sha256)
    assert not (root / "sessions").exists()


@pytest.mark.parametrize("mode", ["inspect-root", "preflight"])
def test_unsupported_native_platform_stops_without_root_or_key_io(tmp_path, monkeypatch, mode):
    root, permit, _, _ = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(capture, "_posix_runtime", lambda: False)
    if mode == "inspect-root":
        monkeypatch.undo()
        monkeypatch.setattr(capture, "_posix_runtime", lambda: False)

    def forbidden(*args, **kwargs):
        raise AssertionError("Unsupported platform must not inspect root/key")

    with monkeypatch.context() as guard:
        guard.setattr(type(os.environ), "get", forbidden)
        guard.setattr(Path, "stat", forbidden)
        with pytest.raises(DemandError, match="^PRIVATE_ACL_PLATFORM_UNSUPPORTED:root$"):
            if mode == "inspect-root":
                operator.inspect_root(root)
            else:
                operator.preflight(permit.review_sha256)


@pytest.mark.parametrize("problem", ["valid", "owner", "public", "readonly"])
def test_explicit_root_inspection_metadata_never_authorizes_capture(
    tmp_path, monkeypatch, capsys, problem
):
    root = tmp_path.resolve()
    original = Path.stat
    info = original(root)
    expected_uid = info.st_uid
    monkeypatch.setattr(capture, "_posix_runtime", lambda: True)
    monkeypatch.setattr(capture, "_owner_uid", lambda: expected_uid)

    def metadata(path, *args, **kwargs):
        actual = original(path, *args, **kwargs)
        if path != root:
            return actual
        return SimpleNamespace(
            st_uid=expected_uid + int(problem == "owner"),
            st_mode={"public": 0o40750, "readonly": 0o40500}.get(problem, 0o40700),
            st_dev=actual.st_dev,
            st_ino=actual.st_ino,
        )

    monkeypatch.setattr(Path, "stat", metadata)
    code = operator.main(["--mode", "inspect-root", "--root", str(root)])
    output = capsys.readouterr().out
    report = json.loads(output)
    assert str(root) not in output and all(report[flag] is False for flag in operator.FLAGS)
    assert report["may_acquire"] is False
    assert capture._ACCEPTED_EXECUTION_REVIEWS == ()
    if problem == "valid":
        assert code == 0 and report["status"] == "inspection_only"
        assert report["root_path_sha256"] == digest(str(root).encode())
        assert "observed_at" in report
    else:
        assert code == 2


def test_plan_encoder_drift_cannot_reuse_review(tmp_path, monkeypatch):
    _, permit, _, _ = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(operator, "PLAN_SHA256", "d" * 64)
    with pytest.raises(DemandError, match="^PLAN_BINDING_MISMATCH:plan$"):
        operator.preflight(permit.review_sha256)


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--mode", "DO_NOT_PRINT"],
        ["--mode", "inspect-root"],
        ["--mode", "capture", "--root", "/DO_NOT_PRINT"],
        ["--mode", "backtest-status", "--root", "/DO_NOT_PRINT"],
        ["--mode", "inspect-root", "--root", "/DO_NOT_PRINT", "--review-sha256", "a" * 64],
        ["--mode", "backtest-status", "--review-sha256", "a" * 64],
    ],
)
def test_cli_arguments_never_echo_untrusted_values(capsys, arguments):
    assert operator.main(arguments) == 2
    output = capsys.readouterr()
    assert "DO_NOT_PRINT" not in output.out + output.err
    assert json.loads(output.out)["code"] == "INVALID_CLI_ARGUMENTS"


def test_backtest_status_is_separate_and_never_reads_root_key_or_archive(monkeypatch, capsys):
    original = type(os.environ).get

    def forbidden(*args, **kwargs):
        raise AssertionError("Capture approval cannot grant research entry")

    def guard_environment(self, key, default=None):
        if key in ("MASSIVE_API_KEY", operator.ROOT_ENVIRONMENT):
            forbidden()
        return original(self, key, default)

    with monkeypatch.context() as guard:
        guard.setattr(type(os.environ), "get", guard_environment)
        guard.setattr(Path, "stat", forbidden)
        guard.setattr(Path, "open", forbidden)
        assert operator.main(["--mode", "backtest-status"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["code"] == "REAL_BACKTEST_VERIFICATION_PENDING"
    assert all(report[flag] is False for flag in operator.FLAGS)


@pytest.mark.parametrize(
    "error",
    [OSError("DO_NOT_PRINT"), TypeError("DO_NOT_PRINT"), DemandError("DO_NOT_PRINT", "private")],
    ids=["filesystem", "type", "unknown_code"],
)
def test_unexpected_backend_errors_are_fixed_and_redacted(monkeypatch, capsys, error):
    def failed(review):
        raise error

    monkeypatch.setattr(operator, "acquire", failed)
    assert operator.main(["--mode", "capture", "--review-sha256", "a" * 64]) == 2
    output = capsys.readouterr().out
    assert "DO_NOT_PRINT" not in output and all(
        json.loads(output)[flag] is False for flag in operator.FLAGS
    )


def test_capture_key_is_released_only_after_scope_and_root(tmp_path, monkeypatch, capsys):
    _, permit, requests, _ = fixture(tmp_path, monkeypatch)
    monkeypatch.delenv("MASSIVE_API_KEY")
    assert operator.main(["--mode", "capture", "--review-sha256", permit.review_sha256]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "CREDENTIAL_UNAVAILABLE"
    assert requests == []


def test_operator_test_ids_fit_windows_environment(request: pytest.FixtureRequest):
    cases = [item.nodeid for item in request.session.items if item.path == Path(__file__).resolve()]
    assert cases and max(map(len, cases)) < 1024
