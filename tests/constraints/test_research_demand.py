"""Invented fixture bytes only; no account evidence, real prices or credential I/O."""

import io
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from quant_constraints.research.data import demand as contracts
from quant_constraints.research.data import demand_capture as capture
from quant_constraints.research.data import demand_storage as storage
from quant_constraints.research.data.archive_contracts import canonical, digest
from quant_constraints.research.data.demand import DemandError, decode_demand_plan, fixed_url
from quant_constraints.research.data.demand_capture import FixtureResponse, FixtureResponses

AS_OF = datetime(2026, 10, 12, 2, tzinfo=UTC)
FLAGS = ("raw_verified", "PIT_verified", "archive_ready", "trusted_entry", "may_build_engine")


def plan(**changes):
    values = {
        "schema_version": contracts.DEMAND_VERSION,
        "capture_id": "synthetic_capture",
        "purpose_ref": "synthetic_missing_raw_pair",
        "storage_root_ref": "synthetic_private_root",
        "provider": "massive",
        "subscription_selected": "stocks_starter",
        "symbol": "AAPL",
        "start_at": "2026-10-09T13:30:00+00:00",
        "end_at": "2026-10-09T13:33:00+00:00",
        "products": ["raw", "comparison"],
        "page_limit": 5000,
        "max_requests": 8,
        "max_source_bytes": 524288,
        "max_total_bytes": 4194304,
        "timeout_seconds": 20,
        "minimum_interval_seconds": 1,
        "retention_days": 30,
        "prior_manifest_sha256": None,
        "refresh_reason_ref": None,
    }
    values.update(changes)
    return canonical(values)


def bar(at, price=100):
    return {
        "t": int(at.timestamp() * 1000),
        "o": price,
        "h": price + 2,
        "l": price - 2,
        "c": price + 1,
        "v": 10,
    }


def body(adjusted=False, rows=None, **changes):
    rows = (
        [
            bar(datetime(2026, 10, 9, 13, 30, tzinfo=UTC)),
            bar(datetime(2026, 10, 9, 13, 32, tzinfo=UTC)),
        ]
        if rows is None
        else rows
    )
    value = {
        "ticker": "AAPL",
        "adjusted": adjusted,
        "status": "OK",
        "queryCount": len(rows),
        "resultsCount": len(rows),
        "request_id": "synthetic_request",
        "results": rows,
    }
    value.update(changes)
    return canonical(value)


def responses(encoded=None, *, paged=False):
    parsed = decode_demand_plan(encoded or plan(), AS_OF)
    records = []
    for product in parsed.products:
        url = fixed_url(parsed, product)
        adjusted = product == "comparison"
        rows = [
            bar(parsed.start_at, 100 if not adjusted else 50),
            bar(parsed.end_at - timedelta(minutes=1), 100 if not adjusted else 50),
        ]
        following = url + "&cursor=" + product + "_page_2"
        pages = (
            [
                (url, body(adjusted, rows[:1], next_url=following)),
                (following, body(adjusted, rows[1:])),
            ]
            if paged
            else [(url, body(adjusted, rows))]
        )
        for address, raw in pages:
            clock = AS_OF - timedelta(minutes=1) + timedelta(seconds=len(records) * 2)
            records.append(FixtureResponse(address, 200, raw, clock, clock + timedelta(seconds=1)))
    return FixtureResponses(tuple(records))


def collect(tmp_path, encoded=None, source=None):
    encoded = encoded or plan()
    source = source or responses(encoded)
    archive = capture.capture_fixture_slice(encoded, tmp_path.resolve(), source, as_of=AS_OF)
    assert archive.manifest.mode == "offline_fixture"
    return archive, source


def rejects(code, call):
    with pytest.raises(DemandError) as error:
        call()
    assert error.value.code == code
    assert "DO_NOT_PRINT" not in str(error.value)


def test_l0_l1_l2_cache_reentry_unknowns_and_no_engine(tmp_path):
    archive, source = collect(tmp_path)
    assert len(source.calls) == 2
    for index, product in enumerate(archive.manifest.products):
        assert archive.read(product.pages[0].response) == source.expected(source.calls[index]).body
        frame = pl.read_parquet(io.BytesIO(archive.read(product.l1)))
        assert frame.height == 2 and frame["volume"].to_list() == [10, 10]
        for column in ("provider_available_at", "published_at", "historically_known_at"):
            assert frame[column].null_count() == frame.height
        assert frame["share_unit"].unique().to_list() == ["unknown"]
        assert "available_at" not in frame.columns
    diagnostics = json.loads(archive.read(archive.manifest.diagnostics))
    assert diagnostics["missing_bars"] == {"raw": 1, "comparison": 1}
    assert all(diagnostics[flag] is False for flag in FLAGS)
    again = capture.capture_fixture_slice(plan(), tmp_path.resolve(), source, as_of=AS_OF)
    assert again.revision == archive.revision and len(source.calls) == 2
    from quant_constraints.research.data.entry import build_research_engine
    from quant_constraints.research.data.verification_contracts import VerificationContractError

    for item in (archive, diagnostics, {**diagnostics, "approved": True, "may_build_engine": True}):
        with pytest.raises(VerificationContractError, match="INVALID_BUNDLE"):
            build_research_engine(item)


def test_finite_pagination_retains_separate_product_bytes(tmp_path):
    archive, source = collect(tmp_path, source=responses(paged=True))
    assert len(source.calls) == 4 and all(len(p.pages) == 2 for p in archive.manifest.products)
    assert all("adjusted=false" in u for u in source.calls[:2])
    assert all("adjusted=true" in u for u in source.calls[2:])
    assert json.loads(archive.read(archive.manifest.ledger))["requests"] == 4


def test_known_snapshot_reuse_and_explicit_refresh_revision(tmp_path):
    first, _ = collect(tmp_path)
    encoded = plan(capture_id="synthetic_reuse", prior_manifest_sha256=first.revision)
    source = responses(encoded)
    reused = capture.capture_fixture_slice(encoded, tmp_path.resolve(), source, as_of=AS_OF)
    assert reused.revision == first.revision and source.calls == []
    encoded = plan(
        capture_id="synthetic_refresh",
        prior_manifest_sha256=first.revision,
        refresh_reason_ref="synthetic_correction_check",
    )
    second, _ = collect(tmp_path, encoded, responses(encoded))
    assert (
        second.revision != first.revision
        and second.manifest.prior_manifest_sha256 == first.revision
    )
    assert (
        json.loads(second.read(second.manifest.diagnostics))["corrections"]
        == "same_supplier_bytes_not_correction_proof"
    )
    encoded = plan(
        capture_id="synthetic_changed",
        prior_manifest_sha256=second.revision,
        refresh_reason_ref="synthetic_correction_check",
    )
    source = responses(encoded)
    changed = replace(
        source._responses[0],
        body=body(False, [bar(datetime(2026, 10, 9, 13, 30, tzinfo=UTC), 101)]),
    )
    third, _ = collect(tmp_path, encoded, FixtureResponses((changed, *source._responses[1:])))
    assert (
        json.loads(third.read(third.manifest.diagnostics))["corrections"]
        == "bytes_differ_not_proof_of_correction"
    )
    assert (
        storage.load_demand_archive(tmp_path.resolve(), first.revision, as_of=AS_OF).revision
        == first.revision
    )


@pytest.mark.parametrize(
    "change,code",
    [
        ({"schema_version": "research_demand_capture_v0"}, "UNSUPPORTED_SCHEMA"),
        ({"products": ["raw"]}, "UNSUPPORTED_DEMAND_SCOPE"),
        ({"products": ["comparison", "raw"]}, "UNSUPPORTED_DEMAND_SCOPE"),
        ({"symbol": "aapl"}, "UNSUPPORTED_DEMAND_SCOPE"),
        ({"page_limit": 0}, "REQUEST_BUDGET_INVALID"),
        ({"page_limit": 50001}, "REQUEST_BUDGET_INVALID"),
        ({"max_requests": 1}, "REQUEST_BUDGET_INVALID"),
        ({"max_requests": 9}, "REQUEST_BUDGET_INVALID"),
        ({"max_source_bytes": 0}, "BYTE_BUDGET_INVALID"),
        ({"max_source_bytes": 524289}, "BYTE_BUDGET_INVALID"),
        ({"max_total_bytes": 1}, "BYTE_BUDGET_INVALID"),
        ({"max_total_bytes": 4194305}, "BYTE_BUDGET_INVALID"),
        ({"timeout_seconds": 0}, "RESOURCE_POLICY_INVALID"),
        ({"timeout_seconds": 31}, "RESOURCE_POLICY_INVALID"),
        ({"minimum_interval_seconds": 61}, "RESOURCE_POLICY_INVALID"),
        ({"retention_days": 0}, "RESOURCE_POLICY_INVALID"),
        ({"retention_days": 31}, "RESOURCE_POLICY_INVALID"),
        ({"refresh_reason_ref": "unneeded_refresh"}, "REFRESH_REQUIRES_PREDECESSOR"),
        ({"end_at": "2026-10-13T13:33:00+00:00"}, "WINDOW_BOUNDS"),
        ({"start_at": "2026-10-01T13:30:00+00:00"}, "WINDOW_BOUNDS"),
        ({"end_at": "2026-10-09T13:30:00+00:00"}, "WINDOW_BOUNDS"),
        ({"start_at": "2026-10-09T13:30:01+00:00"}, "WINDOW_BOUNDS"),
        ({"end_at": "2026-10-09T13:33:00.000001+00:00"}, "WINDOW_BOUNDS"),
        (
            {"start_at": "2026-10-10T13:30:00+00:00", "end_at": "2026-10-10T13:33:00+00:00"},
            "SINGLE_RTH_SESSION_REQUIRED",
        ),
        ({"start_at": "2026-10-09T12:30:00+00:00"}, "SINGLE_RTH_SESSION_REQUIRED"),
        ({"end_at": "2026-10-09T21:30:00+00:00"}, "SINGLE_RTH_SESSION_REQUIRED"),
        ({"purpose_ref": "../DO_NOT_PRINT"}, "UNSAFE_METADATA"),
        ({"approved": True}, "FIELDS_MISMATCH"),
        ({"max_requests": True}, "INVALID_TYPE"),
        ({"prior_manifest_sha256": "DO_NOT_PRINT"}, "UNSAFE_METADATA"),
    ],
)
def test_scope_checked_before_files_or_transport(tmp_path, change, code):
    source = responses()
    rejects(
        code,
        lambda: capture.capture_fixture_slice(
            plan(**change), tmp_path.resolve(), source, as_of=AS_OF
        ),
    )
    assert source.calls == [] and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("value", [None, "unknown", datetime(2026, 10, 12)])
def test_explicit_utc_review_clock(value):
    rejects("INVALID_TIME", lambda: decode_demand_plan(plan(), value))


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"ticker": "MSFT"}, "PRODUCT_SOURCE_CONFLICT"),
        ({"adjusted": True}, "PRODUCT_SOURCE_CONFLICT"),
        ({"adjusted": 0}, "PRODUCT_SOURCE_CONFLICT"),
        ({"status": "ERROR"}, "PRODUCT_SOURCE_CONFLICT"),
        ({"resultsCount": 99}, "PROVIDER_COUNTS_INVALID"),
        ({"queryCount": 5001}, "PROVIDER_COUNTS_INVALID"),
        ({"queryCount": True}, "PROVIDER_COUNTS_INVALID"),
        ({"results": {}}, "PROVIDER_COUNTS_INVALID"),
        ({"request_id": 1}, "PROVIDER_ID_INVALID"),
        ({"request_id": ""}, "PROVIDER_ID_INVALID"),
        ({"request_id": "x" * 257}, "PROVIDER_ID_INVALID"),
        ({"next_url": None}, "PAGINATION_INCOMPLETE"),
        ({"queryCount": 5000}, "PAGINATION_INCOMPLETE"),
        ({"unknown": "DO_NOT_PRINT"}, "PROVIDER_SCHEMA_CONFLICT"),
    ],
)
def test_provider_fields_are_untrusted_declarations(changes, code):
    rejects(
        code,
        lambda: contracts.parse_untrusted_page(
            body(**changes), decode_demand_plan(plan(), AS_OF), "raw"
        ),
    )


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"t": 0}, "BAR_TIME_CONFLICT"),
        ({"t": True}, "BAR_TIME_CONFLICT"),
        ({"t": 1791552600001}, "BAR_TIME_CONFLICT"),
        ({"o": False}, "BAR_VALUE_INVALID"),
        ({"o": 0}, "BAR_VALUE_INVALID"),
        ({"v": -1}, "BAR_VALUE_INVALID"),
        ({"h": 99}, "BAR_VALUE_INVALID"),
        ({"l": 102}, "BAR_VALUE_INVALID"),
        ({"extra": "DO_NOT_PRINT"}, "BAR_SCHEMA_CONFLICT"),
    ],
)
def test_bar_structure_does_not_certify_raw_semantics(changes, code):
    row = bar(datetime(2026, 10, 9, 13, 30, tzinfo=UTC))
    row.update(changes)
    rejects(
        code,
        lambda: contracts.parse_untrusted_page(
            body(rows=[row]), decode_demand_plan(plan(), AS_OF), "raw"
        ),
    )


@pytest.mark.parametrize(
    "bad,code",
    [
        pytest.param(b"{", "INVALID_PROVIDER_JSON", id="broken_json"),
        pytest.param(b'{"a":1,"a":2}', "INVALID_PROVIDER_JSON", id="duplicate_field"),
        pytest.param(b'{"v":NaN}', "INVALID_PROVIDER_JSON", id="nonfinite"),
        pytest.param(b"[]", "PROVIDER_SCHEMA_CONFLICT", id="wrong_root"),
        pytest.param(b'{"token":"DO_NOT_PRINT"}', "KEY_EXPOSURE", id="secret_field"),
    ],
)
def test_provider_json_and_secret_redaction(bad, code):
    rejects(
        code, lambda: contracts.parse_untrusted_page(bad, decode_demand_plan(plan(), AS_OF), "raw")
    )


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "PERMISSION_GAP"),
        (403, "PERMISSION_GAP"),
        (429, "TRANSPORT_STOPPED"),
        (503, "TRANSPORT_STOPPED"),
        (301, "TRANSPORT_STOPPED"),
        (0, "TRANSPORT_STOPPED"),
    ],
)
def test_denial_rate_limit_redirect_error_stop_without_retry(tmp_path, status, code):
    original = responses()
    source = FixtureResponses(
        (replace(original._responses[0], status=status, body=b"{}"), *original._responses[1:])
    )
    rejects(code, lambda: collect(tmp_path, source=source))
    assert len(source.calls) == 1 and not (tmp_path / "revisions").exists()
    assert not list(tmp_path.rglob("*_response.json"))
    rejects("INCOMPLETE_ATTEMPT_REVIEW_REQUIRED", lambda: collect(tmp_path, source=source))
    assert len(source.calls) == 1


def test_request_and_original_byte_limits_include_continuations(tmp_path):
    encoded = plan(max_requests=2)
    source = responses(encoded, paged=True)
    rejects("CAPTURE_BUDGET_EXHAUSTED", lambda: collect(tmp_path, encoded, source))
    assert len(source.calls) == 2 and not (tmp_path / "revisions").exists()
    root = tmp_path / "second"
    root.mkdir(mode=0o700)
    encoded = plan(max_source_bytes=1, max_total_bytes=2)
    source = responses(encoded)
    rejects("RESPONSE_BOUNDS", lambda: collect(root, encoded, source))
    assert len(source.calls) == 1 and not list(root.rglob("*_response.json"))


def test_total_budget_stops_pair_without_dropping_comparison(tmp_path):
    size = len(responses()._responses[0].body)
    encoded = plan(max_source_bytes=size, max_total_bytes=size)
    source = responses(encoded)
    rejects("CAPTURE_BUDGET_EXHAUSTED", lambda: collect(tmp_path, encoded, source))
    assert len(source.calls) == 1 and not (tmp_path / "revisions").exists()


def test_completed_page_cache_resumes_without_double_requests(tmp_path, monkeypatch):
    source = responses(paged=True)
    original = capture.private_write
    state = {"failed": False}

    def interrupt(root, relative, content):
        if relative.endswith("raw_p001_request.json") and not state["failed"]:
            state["failed"] = True
            raise OSError("simulated process interruption")
        return original(root, relative, content)

    monkeypatch.setattr(capture, "private_write", interrupt)
    with pytest.raises(OSError):
        collect(tmp_path, source=source)
    assert len(source.calls) == 1
    archive, source = collect(tmp_path, source=source)
    assert len(source.calls) == 4 and archive.manifest.products[0].row_count == 2


def test_changed_input_cannot_replace_partial_session(tmp_path):
    original = responses()
    source = FixtureResponses(
        (replace(original._responses[0], status=503), *original._responses[1:])
    )
    rejects("TRANSPORT_STOPPED", lambda: collect(tmp_path, source=source))
    fresh = responses()
    rejects("REVISION_CONFLICT", lambda: collect(tmp_path, source=fresh))
    assert fresh.calls == []


@pytest.mark.parametrize("part", ["l0", "l1", "manifest", "inventory"])
def test_replaced_archive_bytes_and_extra_files_fail_closed(tmp_path, part):
    archive, _ = collect(tmp_path)
    directory = tmp_path / "revisions" / archive.revision
    name = {
        "l0": archive.manifest.products[0].pages[0].response.filename,
        "l1": archive.manifest.products[0].l1.filename,
        "manifest": "manifest.json",
        "inventory": "extra.json",
    }[part]
    (directory / name).write_bytes(b"{}")
    with pytest.raises(DemandError):
        storage.load_demand_archive(tmp_path.resolve(), archive.revision, as_of=AS_OF)


def test_retention_expiry_before_reading_original_bodies(tmp_path, monkeypatch):
    archive, _ = collect(tmp_path)
    reads = []
    original = storage.private_read

    def observe(root, path, sha):
        reads.append(path)
        return original(root, path, sha)

    monkeypatch.setattr(storage, "private_read", observe)
    rejects(
        "ARCHIVE_TIME_OR_RETENTION_INVALID",
        lambda: storage.load_demand_archive(
            tmp_path.resolve(), archive.revision, as_of=AS_OF + timedelta(days=31)
        ),
    )
    assert len(reads) == 1 and reads[0].endswith("manifest.json")


def test_real_gate_before_credential_file_network_for_f0_report_or_self_hash(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("no credential, file or network before scope review")

    monkeypatch.setattr(capture.os, "getenv", forbidden)
    monkeypatch.setattr(capture, "build_opener", forbidden)
    monkeypatch.setattr(capture, "private_root", forbidden)
    for value in (None, True, "a" * 64, {"approved": True, "code": "OFFLINE_CONTRACT_PASS"}):
        rejects(
            "EXECUTION_SCOPE_REVIEW_PENDING",
            lambda value=value: capture.capture_real_slice(
                plan(), tmp_path.resolve(), reviewed_execution_sha256=value
            ),
        )
    assert capture._ACCEPTED_EXECUTION_REVIEWS == ()


def test_fixture_cannot_accept_network_callback(tmp_path):
    called = []
    rejects(
        "INVALID_FIXTURE_TRANSPORT",
        lambda: capture.capture_fixture_slice(
            plan(), tmp_path.resolve(), lambda: called.append(True), as_of=AS_OF
        ),
    )
    assert called == [] and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("bad", [None, (), (SimpleNamespace(url="x"),)])
def test_invalid_fixture_shape(bad):
    rejects("INVALID_FIXTURE_TRANSPORT", lambda: FixtureResponses(bad))


def test_sensitive_fixture_not_persisted_or_echoed(tmp_path, capsys):
    source = responses()
    bad = replace(source._responses[0], body=b'{"apiKey":"DO_NOT_PRINT"}')
    rejects("KEY_EXPOSURE", lambda: FixtureResponses((bad,)))
    assert list(tmp_path.iterdir()) == [] and capsys.readouterr() == ("", "")


def test_predecessor_scope_mismatch_and_unbound_file(tmp_path):
    archive, _ = collect(tmp_path)
    encoded = plan(
        capture_id="wrong_scope",
        purpose_ref="different_research",
        prior_manifest_sha256=archive.revision,
    )
    rejects("PREDECESSOR_SCOPE_CONFLICT", lambda: collect(tmp_path, encoded, responses(encoded)))
    rejects(
        "UNBOUND_ARTIFACT",
        lambda: archive.read(replace(archive.manifest.plan, artifact_id="other")),
    )


def test_fixture_capture_uses_no_environment_or_network(tmp_path, monkeypatch):
    source = responses()

    def forbidden(*args, **kwargs):
        raise AssertionError("fixture capture must not use credentials or network")

    monkeypatch.setattr(capture, "build_opener", forbidden)
    original = type(capture.os.environ).get

    def guard_environment(self, key, default=None):
        if "massive" in key.lower():
            forbidden()
        return original(self, key, default)

    monkeypatch.setattr(type(capture.os.environ), "get", guard_environment)
    archive, _ = collect(tmp_path, source=source)
    assert all(
        json.loads(archive.read(archive.manifest.diagnostics))[flag] is False for flag in FLAGS
    )


def native_fixture(tmp_path, monkeypatch):
    """Unit-test private control flow with a fake registry/opener, never real I/O."""
    root = tmp_path.resolve()
    permit = capture._ExecutionPermit(
        "a" * 64,
        digest(plan()),
        digest(str(root).encode()),
        capture.root_identity(root),
        "invented_account",
        "invented_vendor_consent",
        AS_OF + timedelta(hours=1),
    )
    monkeypatch.setattr(capture, "_ACCEPTED_EXECUTION_REVIEWS", (permit,))
    monkeypatch.setattr(capture, "_now", lambda: AS_OF)
    monkeypatch.setattr(capture, "_posix_runtime", lambda: True)
    monkeypatch.setattr(capture, "_owner_uid", lambda: root.stat().st_uid)
    monkeypatch.setenv("MASSIVE_API_KEY", "synthetic_fixture_credential")
    requests = []
    reads = []

    class Response:
        status = 200

        def __init__(self, request):
            self.data = body("adjusted=true" in request.full_url)

        def read(self, size):
            reads.append(size)
            return self.data[:size]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Opener:
        def open(self, request, timeout):
            assert request.get_header("Authorization") == "Bearer synthetic_fixture_credential"
            assert "synthetic_fixture_credential" not in request.full_url
            assert timeout == 20
            requests.append(request.full_url)
            return Response(request)

    monkeypatch.setattr(capture, "build_opener", lambda *_args: Opener())
    monkeypatch.setattr(capture.time, "sleep", lambda _seconds: None)
    return root, permit, requests, reads


def test_native_transport_control_flow_is_fully_mocked_and_stays_untrusted(tmp_path, monkeypatch):
    root, permit, requests, reads = native_fixture(tmp_path, monkeypatch)
    archive = capture.capture_real_slice(
        plan(), root, reviewed_execution_sha256=permit.review_sha256
    )
    assert len(requests) == 2 and all(size <= 524289 for size in reads)
    assert archive.manifest.mode == "licensed_private_unverified"
    assert all(
        json.loads(archive.read(archive.manifest.diagnostics))[flag] is False for flag in FLAGS
    )
    again = capture.capture_real_slice(plan(), root, reviewed_execution_sha256=permit.review_sha256)
    assert again.revision == archive.revision and len(requests) == 2


@pytest.mark.parametrize(
    "problem,code",
    [
        ("plan", "EXECUTION_SCOPE_BINDING_MISMATCH"),
        ("root", "EXECUTION_SCOPE_BINDING_MISMATCH"),
        ("platform", "PRIVATE_ACL_PLATFORM_UNSUPPORTED"),
        ("identity", "PRIVATE_ROOT_CHANGED"),
        ("owner", "PRIVATE_ROOT_CHANGED"),
        ("expiry", "EXECUTION_REVIEW_EXPIRED_OR_REVOKED"),
        ("account", "EXECUTION_REVIEW_EXPIRED_OR_REVOKED"),
        ("license", "EXECUTION_REVIEW_EXPIRED_OR_REVOKED"),
        ("missing_key", "CREDENTIAL_UNAVAILABLE"),
        ("bad_key", "CREDENTIAL_INVALID"),
    ],
)
def test_mocked_execution_registry_still_checks_every_scope_bound(
    tmp_path, monkeypatch, problem, code
):
    root, permit, requests, _ = native_fixture(tmp_path, monkeypatch)
    encoded = plan()
    if problem == "plan":
        encoded = plan(purpose_ref="different")
    if problem == "root":
        root = tmp_path / "unreviewed"
    if problem == "platform":
        monkeypatch.setattr(capture, "_posix_runtime", lambda: False)
    if problem == "identity":
        permit = replace(permit, root_identity_sha256="b" * 64)
    if problem == "owner":
        monkeypatch.setattr(capture, "_owner_uid", lambda: -999)
    if problem == "expiry":
        permit = replace(permit, valid_until=AS_OF)
    if problem == "account":
        permit = replace(permit, account_evidence_ref="")
    if problem == "license":
        permit = replace(permit, license_evidence_ref="")
    if problem == "missing_key":
        monkeypatch.delenv("MASSIVE_API_KEY")
    if problem == "bad_key":
        monkeypatch.setenv("MASSIVE_API_KEY", "bad key")
    monkeypatch.setattr(capture, "_ACCEPTED_EXECUTION_REVIEWS", (permit,))
    rejects(
        code,
        lambda: capture.capture_real_slice(
            encoded, root, reviewed_execution_sha256=permit.review_sha256
        ),
    )
    assert requests == []


@pytest.mark.parametrize("failure", ["http", "timeout", "url_error"])
def test_native_failure_read_bounds_without_network(tmp_path, monkeypatch, failure):
    from urllib.error import HTTPError, URLError

    root, permit, requests, _ = native_fixture(tmp_path, monkeypatch)

    class Opener:
        def open(self, request, timeout):
            requests.append(request.full_url)
            if failure == "http":
                raise HTTPError(request.full_url, 503, "synthetic", {}, io.BytesIO(b"{}"))
            if failure == "timeout":
                raise TimeoutError("DO_NOT_PRINT")
            raise URLError("DO_NOT_PRINT")

    monkeypatch.setattr(capture, "build_opener", lambda *_args: Opener())
    rejects(
        "TRANSPORT_STOPPED",
        lambda: capture.capture_real_slice(
            plan(), root, reviewed_execution_sha256=permit.review_sha256
        ),
    )
    assert len(requests) == 1 and not (root / "revisions").exists()
    rejects(
        "INCOMPLETE_ATTEMPT_REVIEW_REQUIRED",
        lambda: capture.capture_real_slice(
            plan(), root, reviewed_execution_sha256=permit.review_sha256
        ),
    )
    assert len(requests) == 1


def test_native_recheck_revocation_root_and_opaque_cursor_before_fetch(tmp_path, monkeypatch):
    root, permit, requests, _ = native_fixture(tmp_path, monkeypatch)
    parsed = decode_demand_plan(plan(), AS_OF)
    native = capture._NativeResponses(parsed, permit, root, "synthetic_fixture_credential")
    for limit in (0, True, 524289):
        rejects(
            "BYTE_BUDGET_INVALID", lambda limit=limit: native.fetch(fixed_url(parsed, "raw"), limit)
        )
    rejects(
        "PAGINATION_UNVERIFIED",
        lambda: native.fetch(fixed_url(parsed, "raw") + "&cursor=opaque", 100),
    )
    rejects("PAGE_INVALID", lambda: native.fetch("https://elsewhere.invalid/DO_NOT_PRINT", 100))
    monkeypatch.setattr(capture, "_ACCEPTED_EXECUTION_REVIEWS", ())
    rejects("EXECUTION_REVIEW_EXPIRED_OR_REVOKED", native.check_scope)
    monkeypatch.setattr(capture, "_ACCEPTED_EXECUTION_REVIEWS", (permit,))
    monkeypatch.setattr(capture, "root_identity", lambda _root: "different")
    rejects("PRIVATE_ROOT_CHANGED", native.check_scope)
    assert requests == []


@pytest.mark.parametrize(
    "case,code",
    [
        ("loop", "PAGINATION_LOOP"),
        ("range", "PAGINATION_RANGE_CONFLICT"),
        ("duplicate", "BAR_ORDER_OR_DUPLICATE"),
        ("order", "BAR_ORDER_OR_DUPLICATE"),
        ("wrong_next", "PAGINATION_CHAIN_CONFLICT"),
        ("native_cursor", "PAGINATION_UNVERIFIED"),
    ],
)
def test_pagination_chain_rejects_loops_order_changes_and_unreviewed_cursors(case, code):
    parsed = decode_demand_plan(plan(), AS_OF)
    chain = contracts.UntrustedChain(parsed, "raw", fixture=case != "native_cursor")
    url = fixed_url(parsed, "raw")
    rows = [bar(parsed.start_at), bar(parsed.end_at - timedelta(minutes=1))]
    value = json.loads(body(rows=rows))
    address = url
    if case == "loop":
        value["next_url"] = url
    if case == "range":
        value["next_url"] = url.replace(
            str(int(parsed.start_at.timestamp() * 1000)),
            str(int((parsed.start_at + timedelta(minutes=1)).timestamp() * 1000)),
        )
    if case == "duplicate":
        value["results"] = [rows[0], rows[0]]
    if case == "order":
        value["results"] = list(reversed(rows))
    if case == "wrong_next":
        address = url + "&cursor=unexpected"
    if case == "native_cursor":
        value["next_url"] = url + "&cursor=opaque"
    rejects(code, lambda: chain.accept(address, value))


def test_cursor_advance_and_empty_sparse_data_are_not_completeness_or_pit(tmp_path):
    parsed = decode_demand_plan(plan(), AS_OF)
    chain = contracts.UntrustedChain(parsed, "raw", fixture=True)
    url = fixed_url(parsed, "raw")
    following = url.replace(
        str(int(parsed.start_at.timestamp() * 1000)),
        str(int((parsed.start_at + timedelta(minutes=1)).timestamp() * 1000)),
    )
    page = json.loads(body(rows=[bar(parsed.start_at)], next_url=following))
    chain.accept(url, page)
    chain.accept(following, json.loads(body(rows=[bar(parsed.end_at - timedelta(minutes=1))])))
    assert chain.next_url is None
    original = responses()
    source = FixtureResponses(
        tuple(replace(r, body=body(index == 1, [])) for index, r in enumerate(original._responses))
    )
    archive, _ = collect(tmp_path, source=source)
    assert all(p.row_count == 0 for p in archive.manifest.products)
    assert json.loads(archive.read(archive.manifest.diagnostics))["missing_bars"] == {
        "raw": 3,
        "comparison": 3,
    }
    rejects(
        "PAGINATION_INCOMPLETE",
        lambda: contracts.parse_untrusted_page(body(rows=[], queryCount=1), parsed, "raw"),
    )
    rejects(
        "BAR_SCHEMA_CONFLICT",
        lambda: contracts.parse_untrusted_page(body(rows=[{}]), parsed, "raw"),
    )
    huge = bar(parsed.start_at)
    huge["o"] = 10**400
    rejects(
        "BAR_VALUE_INVALID",
        lambda: contracts.parse_untrusted_page(body(rows=[huge]), parsed, "raw"),
    )


def test_fixture_duplicate_url_unknown_response_and_bad_clocks(tmp_path):
    original = responses()
    rejects(
        "DUPLICATE_FIXTURE_URL",
        lambda: FixtureResponses((original._responses[0], original._responses[0])),
    )
    rejects(
        "INVALID_FIXTURE_CLOCK",
        lambda: FixtureResponses(
            (replace(original._responses[0], started_at=datetime(2026, 10, 12)),)
        ),
    )
    source = FixtureResponses((original._responses[0],))
    rejects("FIXTURE_RESPONSE_MISSING", lambda: collect(tmp_path, source=source))


@pytest.mark.parametrize("which", ["reverse", "future", "before_window", "page_order"])
def test_capture_clock_conflicts_never_publish(tmp_path, which):
    source = responses()
    values = list(source._responses)
    if which == "reverse":
        values[0] = replace(values[0], started_at=AS_OF, ingested_at=AS_OF - timedelta(seconds=1))
    if which == "future":
        values[0] = replace(values[0], ingested_at=AS_OF + timedelta(seconds=1))
    if which == "before_window":
        values[0] = replace(values[0], started_at=datetime(2026, 10, 1, tzinfo=UTC))
    if which == "page_order":
        values[1] = replace(values[1], started_at=values[0].started_at - timedelta(seconds=1))
    rejects(
        "CAPTURE_CLOCK_CONFLICT", lambda: collect(tmp_path, source=FixtureResponses(tuple(values)))
    )
    assert not (tmp_path / "revisions").exists()


def test_manifest_contract_and_publishing_scope_not_self_hash_acceptance(tmp_path):
    archive, _ = collect(tmp_path)
    content = {b.artifact_id: archive.read(b) for b in archive.manifest.bindings()}
    rejects(
        "PUBLICATION_BINDING_MISMATCH",
        lambda: storage.publish_demand_archive(
            tmp_path.resolve(), archive.manifest, {**content, "extra": b"{}"}, as_of=AS_OF
        ),
    )
    for value in (None, "A" * 64, "not_a_digest"):
        rejects(
            "INVALID_DIGEST",
            lambda value=value: storage.load_demand_archive(tmp_path.resolve(), value, as_of=AS_OF),
        )


def save_forged_manifest(root, value, raw=False):
    encoded = json.dumps(value).encode() if raw else canonical(value)
    sha = digest(encoded)
    directory = root / "revisions" / sha
    directory.mkdir(mode=0o700)
    (directory / "manifest.json").write_bytes(encoded)
    return sha


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("version", "UNSUPPORTED_SCHEMA"),
        ("empty_pages", "ARCHIVE_INVENTORY_INVALID"),
        ("many_rows", "ARCHIVE_INVENTORY_INVALID"),
        ("wrong_role", "ARCHIVE_ROLE_CONFLICT"),
        ("zero_size", "ARCHIVE_INVENTORY_INVALID"),
        ("huge_size", "ARCHIVE_INVENTORY_INVALID"),
        ("products", "ARCHIVE_INVENTORY_INVALID"),
        ("duplicate_binding", "ARCHIVE_INVENTORY_INVALID"),
        ("noncanonical", "NONCANONICAL_MANIFEST"),
    ],
)
def test_manifest_versions_roles_sizes_and_inventory(mutation, code, tmp_path):
    archive, _ = collect(tmp_path)
    value = asdict(archive.manifest)
    value = json.loads(canonical(value))
    if mutation == "version":
        value["schema_version"] = "research_demand_archive_v0"
    if mutation == "empty_pages":
        value["products"][0]["pages"] = []
    if mutation == "many_rows":
        value["products"][0]["row_count"] = 391
    if mutation == "wrong_role":
        value["plan"]["role"] = "receipt"
    if mutation == "zero_size":
        value["plan"]["size_bytes"] = 0
    if mutation == "huge_size":
        value["plan"]["size_bytes"] = 2097153
    if mutation == "products":
        value["products"] = []
    if mutation == "duplicate_binding":
        value["ledger"] = value["plan"]
    sha = save_forged_manifest(tmp_path, value, raw=mutation == "noncanonical")
    rejects(code, lambda: storage.load_demand_archive(tmp_path.resolve(), sha, as_of=AS_OF))


def test_private_root_repository_alias_hardlink_and_acl_boundaries(tmp_path, monkeypatch):
    rejects("PRIVATE_ROOT_INVALID", lambda: storage.private_root(None))
    (tmp_path / ".git").mkdir()
    rejects("ROOT_INSIDE_REPOSITORY", lambda: storage.private_root(tmp_path.resolve()))
    (tmp_path / ".git").rmdir()
    monkeypatch.setattr(Path, "is_symlink", lambda _self: True)
    rejects("PATH_INVALID", lambda: storage.private_root(tmp_path.resolve()))
    monkeypatch.undo()
    path = tmp_path / "checkpoint.json"
    path.write_bytes(b"{}")
    import os

    os.link(path, tmp_path / "alias.json")
    rejects(
        "CHECKPOINT_INVALID", lambda: storage.read_checkpoint(tmp_path.resolve(), "checkpoint.json")
    )
    (tmp_path / "alias.json").unlink()
    if os.name == "posix":
        tmp_path.chmod(0o755)
        rejects("PRIVATE_ACL_REQUIRED", lambda: storage.private_root(tmp_path.resolve()))
        tmp_path.chmod(0o700)
        private = tmp_path / "public_dir"
        private.mkdir(mode=0o755)
        rejects(
            "PRIVATE_ACL_REQUIRED",
            lambda: storage.artifact_path(tmp_path.resolve(), "public_dir/file.json"),
        )


def test_checkpoint_and_filesystem_failures_only_fixed_diagnostics(tmp_path, monkeypatch):
    rejects(
        "CHECKPOINT_INVALID", lambda: storage.read_checkpoint(tmp_path.resolve(), "missing.json")
    )

    def failure(*args, **kwargs):
        raise OSError("DO_NOT_PRINT")

    monkeypatch.setattr(storage, "write_once", failure)
    rejects(
        "PRIVATE_WRITE_FAILED",
        lambda: storage.private_write(tmp_path.resolve(), "file.json", b"{}"),
    )
    monkeypatch.undo()
    path = tmp_path / "checkpoint.json"
    path.write_bytes(b" " * 32769)
    rejects(
        "CHECKPOINT_INVALID", lambda: storage.read_checkpoint(tmp_path.resolve(), "checkpoint.json")
    )


@pytest.mark.parametrize("surface", ["descriptor", "path"])
@pytest.mark.parametrize(
    "changed",
    [
        "st_atime_ns",
        "st_dev",
        "st_ino",
        "st_size",
        "st_mtime_ns",
        "st_mode",
        "st_nlink",
        "st_uid",
        "st_gid",
        "st_ctime_ns",
    ],
)
def test_checkpoint_read_atime_is_not_mutation_but_integrity_changes_stop(
    tmp_path, monkeypatch, surface, changed
):
    root = tmp_path.resolve()
    path = root / "checkpoint.json"
    path.write_bytes(b"{}")
    actual_fstat, actual_lstat = storage.os.fstat, Path.lstat
    calls = 0

    def change(item):
        names = (
            "st_atime_ns",
            "st_dev",
            "st_ino",
            "st_size",
            "st_mtime_ns",
            "st_mode",
            "st_nlink",
            "st_uid",
            "st_gid",
            "st_ctime_ns",
        )
        values = {name: getattr(item, name) for name in names}
        values[changed] += 1
        return SimpleNamespace(**values)

    def fstat(descriptor):
        nonlocal calls
        calls += 1
        item = actual_fstat(descriptor)
        return change(item) if surface == "descriptor" and calls > 1 else item

    def lstat(self, *args, **kwargs):
        item = actual_lstat(self, *args, **kwargs)
        return change(item) if surface == "path" and self == path and calls > 1 else item

    monkeypatch.setattr(storage.os, "fstat", fstat)
    monkeypatch.setattr(Path, "lstat", lstat)
    if changed == "st_atime_ns":
        assert storage.read_checkpoint(root, "checkpoint.json") == b"{}"
    else:
        rejects("CHECKPOINT_CHANGED", lambda: storage.read_checkpoint(root, "checkpoint.json"))


def test_checkpoint_windows_stat_fstat_ctime_difference_is_not_mutation(tmp_path, monkeypatch):
    path = tmp_path / "checkpoint.json"
    path.write_bytes(b"{}")
    actual = storage.os.fstat

    def fstat(descriptor):
        item = actual(descriptor)
        names = (
            "st_dev",
            "st_ino",
            "st_size",
            "st_mtime_ns",
            "st_mode",
            "st_nlink",
            "st_uid",
            "st_gid",
            "st_ctime_ns",
        )
        values = {name: getattr(item, name) for name in names}
        values["st_ctime_ns"] += 100000
        return SimpleNamespace(**values)

    monkeypatch.setattr(storage.os, "fstat", fstat)
    assert storage.read_checkpoint(tmp_path.resolve(), "checkpoint.json") == b"{}"


def test_native_archive_use_revocation_and_live_manifest_copy_refuse_reads(tmp_path, monkeypatch):
    root, permit, requests, _ = native_fixture(tmp_path, monkeypatch)
    archive = capture.capture_real_slice(
        plan(), root, reviewed_execution_sha256=permit.review_sha256
    )
    binding = archive.manifest.products[0].pages[0].response
    monkeypatch.setattr(capture, "_ACCEPTED_EXECUTION_REVIEWS", ())
    rejects("EXECUTION_REVIEW_EXPIRED_OR_REVOKED", lambda: archive.read(binding))
    monkeypatch.setattr(
        capture, "_ACCEPTED_EXECUTION_REVIEWS", (replace(permit, root_path_sha256="b" * 64),)
    )
    rejects("EXECUTION_SCOPE_BINDING_MISMATCH", lambda: archive.read(binding))
    forged = replace(
        archive,
        manifest=replace(archive.manifest, mode="offline_fixture", execution_review_sha256=None),
    )
    rejects("LIVE_MANIFEST_CHANGED", lambda: forged.read(binding))
    assert len(requests) == 2


def test_corrupt_checkpoint_and_unknown_audit_values_never_echo(tmp_path):
    archive, source = collect(tmp_path)
    completed = next(tmp_path.rglob("completed.json"))
    completed.write_bytes(b"{")
    rejects("CHECKPOINT_INVALID", lambda: collect(tmp_path, source=source))
    completed.write_bytes(
        canonical({"binding_sha256": "wrong", "manifest_sha256": archive.revision})
    )
    rejects("SESSION_BINDING_MISMATCH", lambda: collect(tmp_path, source=source))
    rejects("INVALID_AUDIT_ARGUMENT", lambda: contracts.demand_audit("DO_NOT_PRINT"))
    rejects(
        "INVALID_TIME",
        lambda: storage.load_demand_archive(tmp_path.resolve(), archive.revision, as_of=None),
    )


def test_malformed_provider_status_and_fixture_metadata_bounds_are_stable():
    rejects(
        "PRODUCT_SOURCE_CONFLICT",
        lambda: contracts.parse_untrusted_page(
            body(status={"unsafe": "DO_NOT_PRINT"}), decode_demand_plan(plan(), AS_OF), "raw"
        ),
    )
    sample = responses()._responses[0]
    for changes in ({"url": "x" * 4097}, {"status": 600}, {"body": b" " * 2097153}):
        rejects(
            "INVALID_FIXTURE_TRANSPORT",
            lambda changes=changes: FixtureResponses((replace(sample, **changes),)),
        )


def test_documented_candidate_is_one_rth_day_and_still_has_no_execution_permit():
    encoded = (
        Path(__file__).resolve().parents[2] / "docs/examples/research-demand-aapl-v1.json"
    ).read_bytes()
    candidate = decode_demand_plan(encoded, AS_OF)
    assert candidate.symbol == "AAPL" and candidate.products == ("raw", "comparison")
    assert candidate.end_at - candidate.start_at == timedelta(hours=6, minutes=30)
    assert candidate.max_requests == 8 and candidate.max_total_bytes == 4194304
    assert capture._ACCEPTED_EXECUTION_REVIEWS == ()


@pytest.mark.parametrize(
    "case,code",
    [
        ("nonstring", "PAGE_INVALID"),
        ("oversize", "PAGE_INVALID"),
        ("space", "PAGE_INVALID"),
        ("bad_percent", "PAGE_INVALID"),
        ("invalid_query", "PAGE_INVALID"),
        ("http", "PAGE_INVALID"),
        ("host", "PAGE_INVALID"),
        ("fragment", "PAGE_INVALID"),
        ("symbol", "PAGE_INVALID"),
        ("apikey", "KEY_EXPOSURE"),
        ("duplicate", "PAGE_INVALID"),
        ("extra", "PAGE_INVALID"),
        ("adjusted", "PAGE_INVALID"),
        ("order", "PAGE_INVALID"),
        ("limit", "PAGE_INVALID"),
        ("bad_cursor", "PAGE_INVALID"),
        ("missing_semantics", "PAGE_INVALID"),
        ("bad_boundary", "PAGE_INVALID"),
        ("outside", "PAGE_INVALID"),
        ("wrong_end", "PAGE_INVALID"),
        ("unaligned", "PAGE_INVALID"),
    ],
)
def test_demand_urls_pin_origin_product_range_and_query(case, code):
    p = decode_demand_plan(plan(), AS_OF)
    url = fixed_url(p, "raw")
    start = str(int(p.start_at.timestamp() * 1000))
    end = str(int(p.end_at.timestamp() * 1000) - 1)
    mutations = {
        "nonstring": None,
        "oversize": "x" * 4097,
        "space": url + " ",
        "bad_percent": url + "&cursor=%XX",
        "invalid_query": url + "&broken",
        "http": url.replace("https:", "http:"),
        "host": url.replace("api.massive.com", "api.massive.com.evil.invalid"),
        "fragment": url + "#secret",
        "symbol": url.replace("AAPL", "MSFT"),
        "apikey": url + "&apiKey=DO_NOT_PRINT",
        "duplicate": url + "&sort=asc",
        "extra": url + "&arbitrary=1",
        "adjusted": url.replace("adjusted=false", "adjusted=true"),
        "order": url.replace("sort=asc", "sort=desc"),
        "limit": url.replace("limit=5000", "limit=1"),
        "bad_cursor": url + "&cursor=bad%21",
        "missing_semantics": url.split("?")[0],
        "bad_boundary": url.replace(start, "invalid"),
        "outside": url.replace(start, "0"),
        "wrong_end": url.replace(end, str(int(end) - 60000)),
        "unaligned": url.replace(start, str(int(start) + 1)),
    }
    rejects(code, lambda: contracts.checked_scope(mutations[case], p, "raw"))
