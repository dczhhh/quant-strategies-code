"""Invented instrument, provider and Git fixtures; no real data or credentials."""

import base64
import io
import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from urllib.error import HTTPError, URLError

import polars as pl
import pytest

from quant_constraints.research.data import instrument_policy as policy
from quant_constraints.research.data import private_sample_batch as batch
from quant_constraints.research.data import sample_contracts as contracts
from quant_constraints.research.data.archive_contracts import canonical, digest
from quant_constraints.research.data.demand import DemandError
from quant_constraints.research.data.private_repo_ingest import COLUMNS

AS_OF = datetime(2026, 10, 13, 8, tzinfo=UTC)
SLICES = contracts.sample_slices()


def invented_rows(spec, count=None, scale=1):
    count = spec.expected_minutes if count is None else count
    return [
        dict(
            zip(
                COLUMNS,
                [
                    int(spec.start_at.timestamp() * 1000) + i * 60000,
                    100 * scale,
                    101 * scale,
                    99 * scale,
                    100.5 * scale,
                    50,
                ],
            )
        )
        for i in range(count)
    ]


def provider(spec, product, count=None):
    scale = 10 if spec.purpose == "split_before" and product == "raw" else 1
    rows = invented_rows(spec, count, scale)
    return {
        "ticker": spec.symbol,
        "adjusted": product == "comparison",
        "status": "OK",
        "queryCount": len(rows),
        "resultsCount": len(rows),
        "count": len(rows),
        "request_id": "invented_request",
        "results": [dict(zip(("t", "o", "h", "l", "c", "v"), list(r.values()))) for r in rows],
    }


def register(monkeypatch, evidence):
    monkeypatch.setattr(
        policy, "_REVIEWED_EVIDENCE", (*policy._REVIEWED_EVIDENCE, digest(canonical(evidence)))
    )


def synthetic_evidence(**changes):
    evidence = contracts.security_evidence("AAPL")
    evidence.update(symbol="TEST", cap_basis="total_market_cap", cap_lower_bound_usd=5_000_000_000)
    evidence.update(changes)
    return evidence


def admit(evidence, rationale=None, symbol=None, day=None, now=AS_OF, plan="a" * 64):
    return policy.admit_instrument(
        symbol or evidence["symbol"], day or date(2026, 3, 6), plan, evidence, now, rationale
    )


def rationale():
    return dict.fromkeys(
        (
            "hypothesis",
            "why_no_largecap_or_etf_substitute",
            "liquidity",
            "identity_and_cap_basis",
            "exact_request_scope",
            "budget_and_exit_cache_rules",
        ),
        "Invented explicit substantive review evidence; never production approval.",
    )


def review(evidence, justification):
    return {
        "symbol": evidence["symbol"],
        "plan_sha256": "a" * 64,
        "evidence_sha256": digest(canonical(evidence)),
        "justification_sha256": digest(canonical(justification)),
        "review_ref": "https://github.com/dczhhh/quant-strategies/pull/999#pullrequestreview-999",
        "reviewed_at": (AS_OF - timedelta(days=1)).isoformat(),
        "expires_at": (AS_OF + timedelta(days=1)).isoformat(),
    }


@pytest.mark.parametrize("spec", SLICES)
def test_seven_frozen_plans_admit_expected_largecaps_or_actual_etf(spec):
    result = admit(contracts.security_evidence(spec.symbol), day=spec.day, plan=spec.plan_sha256)
    assert result["category"] == ("etf" if spec.symbol == "SPY" else "large_cap")
    assert result["historical_universe_PIT_verified"] is False
    assert len(spec.plan_sha256) == 64 and contracts.sample_slice(spec.capture_id) == spec
    assert spec.url("raw").startswith("https://api.massive.com/v2/aggs/ticker/" + spec.symbol)
    assert sum(len(s.products) for s in SLICES) == 12
    assert sum(s.expected_minutes * len(s.products) for s in SLICES) == 4320
    assert contracts.frozen_batch()["max_source_reserved_bytes"] == 14 * 524288


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"symbol": "GME", "cap_lower_bound_usd": 999_000_000_000}, "DENIED_MEME"),
        ({"security_id": "CIK:0001326380", "security_type": "ETF"}, "DENIED_MEME"),
        ({"wsb_status": "confirmed_meme"}, "DENIED_MEME"),
        ({"cap_lower_bound_usd": 100_000_000}, "DENIED_MEME"),
        ({"security_type": "ETN"}, "SECURITY_TYPE_UNVERIFIED"),
        ({"security_type": "CEF"}, "SECURITY_TYPE_UNVERIFIED"),
        ({"security_type": None}, "SECURITY_TYPE_UNVERIFIED"),
        ({"security_id": "UNKNOWN"}, "STOCK_IDENTITY"),
        ({"wsb_status": "unknown"}, "INSTRUMENT_SCOPE"),
        ({"symbol": "OTHER"}, "MIDCAP_JUSTIFICATION"),
        ({"currency": "EUR"}, "INSTRUMENT_SCOPE"),
        ({"source_kind": "caller_provided"}, "INSTRUMENT_SCOPE"),
        ({"historical_universe_PIT_verified": True}, "INSTRUMENT_SCOPE"),
        ({"valid_from": "2026-03-07"}, "INSTRUMENT_SCOPE"),
        ({"valid_to": "2026-03-05"}, "INSTRUMENT_SCOPE"),
        ({"reviewed_on": "2027-01-01"}, "INSTRUMENT_SCOPE"),
        ({"cap_lower_bound_usd": True}, "CAPITALIZATION_EVIDENCE"),
        ({"cap_lower_bound_usd": 0}, "CAPITALIZATION_EVIDENCE"),
        ({"cap_basis": "unknown"}, "CAPITALIZATION_EVIDENCE"),
        ({"cap_basis_date": "2026-03-07"}, "CAPITALIZATION_DATE"),
        ({"cap_basis_date": "2024-01-01"}, "CAPITALIZATION_DATE"),
        ({"cap_lower_bound_usd": 10_000_000_000}, "CAPITALIZATION_BOUNDARY"),
        ({"cap_lower_bound_usd": 2_000_000_000}, "CAPITALIZATION_BOUNDARY"),
        ({"cap_basis": "nonaffiliate_lower_bound"}, "CAPITALIZATION_CLASS"),
        ({"security_type": "ETF", "cap_basis": "issuer_etf"}, "ETF_IDENTITY"),
    ],
)
def test_policy_rejects_meme_alias_small_unknown_boundary_and_unverifiable_metadata(
    monkeypatch, changes, code
):
    evidence = synthetic_evidence(**changes)
    register(monkeypatch, evidence)
    with pytest.raises(DemandError, match=code):
        admit(evidence)


@pytest.mark.parametrize(
    "argument,value",
    [
        ("symbol", "gme"),
        ("symbol", None),
        ("day", "2026-03-06"),
        ("plan", None),
        ("plan", "x"),
        ("now", AS_OF.replace(tzinfo=None)),
        ("evidence", None),
    ],
)
def test_invalid_policy_inputs_fail_closed(argument, value):
    values = {
        "symbol": "AAPL",
        "day": date(2026, 3, 6),
        "plan_sha256": "a" * 64,
        "evidence": contracts.security_evidence("AAPL"),
        "as_of": AS_OF,
    }
    values[{"plan": "plan_sha256", "now": "as_of"}.get(argument, argument)] = value
    with pytest.raises(DemandError, match="INSTRUMENT_POLICY_INPUT_INVALID"):
        policy.admit_instrument(**values)


def test_unregistered_evidence_and_identity_change_cannot_self_authorize():
    with pytest.raises(DemandError, match="UNREVIEWED_INSTRUMENT"):
        admit(synthetic_evidence())
    with pytest.raises(DemandError, match="INSTRUMENT_SCOPE"):
        admit(contracts.security_evidence("AAPL"), symbol="NVDA")
    assert policy._ACCEPTED_MIDCAP_REVIEWS == ()


@pytest.mark.parametrize("reason", [None, {}, {"hypothesis": "short"}])
def test_midcap_without_substantive_rationale_stops(monkeypatch, reason):
    evidence = synthetic_evidence()
    register(monkeypatch, evidence)
    with pytest.raises(DemandError, match="MIDCAP_JUSTIFICATION_REQUIRED"):
        admit(evidence, reason)


@pytest.mark.parametrize(
    "field",
    [
        None,
        "symbol",
        "plan_sha256",
        "evidence_sha256",
        "justification_sha256",
        "review_ref",
        "reviewed_at",
        "expires_at",
    ],
)
def test_midcap_requires_independently_bound_review_not_algorithm_self_approval(monkeypatch, field):
    evidence, reason = synthetic_evidence(), rationale()
    register(monkeypatch, evidence)
    with pytest.raises(DemandError, match="MIDCAP_INDEPENDENT_REVIEW_REQUIRED"):
        admit(evidence, reason)
    accepted = review(evidence, reason)
    if field is not None:
        accepted[field] = (
            {
                "reviewed_at": (AS_OF + timedelta(days=1)).isoformat(),
                "expires_at": (AS_OF - timedelta(days=1)).isoformat(),
            }
        ).get(field, "mismatch")
    monkeypatch.setattr(policy, "_ACCEPTED_MIDCAP_REVIEWS", (accepted,))
    if field is None:
        assert admit(evidence, reason)["category"] == "mid_cap_independently_reviewed"
    else:
        with pytest.raises(DemandError, match="MIDCAP_INDEPENDENT_REVIEW_REQUIRED"):
            admit(evidence, reason)


@pytest.mark.parametrize("spec", SLICES)
def test_original_bytes_canonical_tables_session_calendar_and_partial_gaps(spec):
    body = json.dumps(provider(spec, "raw"), indent=2).encode()
    rows = contracts.source_rows(spec, body, "raw")
    csv, parquet = contracts.encode_rows(spec, rows)
    assert contracts.decode_csv(spec, csv) == rows
    assert pl.read_parquet(io.BytesIO(parquet)).height == spec.expected_minutes
    tables = dict.fromkeys(spec.products, rows)
    assert contracts.structural_checks(spec, tables)["minute_coverage_complete"] is True
    partial = contracts.structural_checks(spec, dict.fromkeys(spec.products, rows[:-1]))
    assert partial["minute_coverage_complete"] is False and partial["missing_bars_filled"] is False
    empty_csv, empty_parquet = contracts.encode_rows(spec, [])
    assert (
        contracts.decode_csv(spec, empty_csv) == []
        and pl.read_parquet(io.BytesIO(empty_parquet)).height == 0
    )
    assert "adjusted=false" in spec.url("raw")
    if len(spec.products) == 2:
        assert "adjusted=true" in spec.url("comparison")


@pytest.mark.parametrize(
    "problem",
    [
        "unknown_slice",
        "unknown_product",
        "too_many",
        "not_list",
        "schema",
        "bool_timestamp",
        "outside",
        "end",
        "alignment",
        "duplicate",
        "reverse",
        "nan",
        "bool_price",
        "negative_volume",
        "zero_price",
        "inverted",
        "count",
        "request_id",
        "otc",
        "trades",
        "vw",
        "pagination",
        "extra_metadata",
        "size",
        "csv_header",
        "csv_extra",
        "csv_null",
        "csv_many",
        "products",
    ],
)
def test_codec_rejection_paths(problem):
    spec = SLICES[0]
    rows = invented_rows(spec, 2)
    page = provider(spec, "raw", 2)
    with pytest.raises((DemandError, ValueError, TypeError)):
        if problem == "unknown_slice":
            contracts.sample_slice("arbitrary")
        elif problem == "unknown_product":
            spec.url("other")
        elif problem in {
            "count",
            "request_id",
            "otc",
            "trades",
            "vw",
            "pagination",
            "extra_metadata",
            "size",
        }:
            if problem == "count":
                page["count"] = True
            if problem == "request_id":
                page["request_id"] = "not allowed!"
            if problem == "otc":
                page["results"][0]["otc"] = True
            if problem == "trades":
                page["results"][0]["n"] = -1
            if problem == "vw":
                page["results"][0]["vw"] = -1
            if problem == "pagination":
                page["next_url"] = spec.url("raw") + "&cursor=synthetic"
            if problem == "extra_metadata":
                page["unknown"] = 1
            contracts.source_rows(
                spec, b" " * (batch.FILE_LIMIT + 1) if problem == "size" else canonical(page), "raw"
            )
        elif problem.startswith("csv_"):
            csv, _ = contracts.encode_rows(spec, rows)
            if problem == "csv_header":
                csv = csv.replace(b"open", b"other")
            if problem == "csv_extra":
                csv = csv.replace(b"50.0\n", b"50.0,1\n")
            if problem == "csv_null":
                csv = csv.replace(b",50.0", b"")
            if problem == "csv_many":
                csv = csv.splitlines(keepends=True)[0] + csv.splitlines(keepends=True)[1] * (
                    spec.expected_minutes + 1
                )
            contracts.decode_csv(spec, csv)
        elif problem == "products":
            contracts.structural_checks(spec, {})
        else:
            if problem == "too_many":
                rows *= 200
            if problem == "not_list":
                rows = tuple(rows)
            if problem == "schema":
                rows[0]["unknown"] = 1
            if problem == "bool_timestamp":
                rows[0]["timestamp_ms"] = True
            if problem == "outside":
                rows[0]["timestamp_ms"] -= 60000
            if problem == "end":
                rows[1]["timestamp_ms"] = int(spec.end_at.timestamp() * 1000)
            if problem == "alignment":
                rows[0]["timestamp_ms"] += 1
            if problem == "duplicate":
                rows[1]["timestamp_ms"] = rows[0]["timestamp_ms"]
            if problem == "reverse":
                rows.reverse()
            if problem == "nan":
                rows[0]["open"] = float("nan")
            if problem == "bool_price":
                rows[0]["open"] = True
            if problem == "negative_volume":
                rows[0]["volume"] = -1
            if problem == "zero_price":
                rows[0]["low"] = 0
            if problem == "inverted":
                rows[0]["high"] = 98
            contracts.validate_rows(spec, rows)


def test_missing_count_alias_optional_provider_fields_and_frozen_drift(monkeypatch):
    spec = SLICES[0]
    page = provider(spec, "raw", 1)
    del page["count"]
    page["results"][0].update(otc=False, n=2, vw=100.0)
    assert len(contracts.source_rows(spec, canonical(page), "raw")) == 1
    monkeypatch.setattr(contracts, "FROZEN_SHA256", "0" * 64)
    with pytest.raises(DemandError, match="FROZEN_SAMPLE_PLAN_CHANGED"):
        contracts.frozen_batch()


def test_calendar_resource_conflict_is_not_overridden(monkeypatch):
    frozen = contracts.frozen_batch()
    frozen["slices"][0]["expected_minutes"] = 391
    monkeypatch.setattr(contracts, "frozen_batch", lambda: frozen)
    with pytest.raises(DemandError, match="SAMPLE_CALENDAR_CONFLICT"):
        contracts.sample_slices()
    with pytest.raises(DemandError, match="UNREVIEWED_INSTRUMENT_EVIDENCE"):
        contracts.security_evidence("UNKNOWN")


class GitFixture:
    """Stateful invented Git DAG with separate commits/trees, not one mutable tree."""

    def __init__(self):
        self._token = "fixture_github_key"
        self.refs, self.blobs, self.trees = {}, {}, {"b" * 40: {}}
        self.commits = {"a" * 40: {"tree": {"sha": "b" * 40}, "parents": []}}
        self.problem = None
        self.writes = []

    def private(self):
        if self.problem == "public":
            raise DemandError("PRIVATE_REPOSITORY_REQUIRED", "scope")

    def request(self, method, path):
        assert method == "GET"
        if path.startswith("/git/ref/heads/"):
            ref = self.refs.get(path.removeprefix("/git/ref/heads/"))
            return None if ref is None else {"object": {"sha": ref}}
        if path.startswith("/git/commits/"):
            return deepcopy(self.commits.get(path.rsplit("/", 1)[1]))
        if path.startswith("/git/trees/"):
            mapping = self.trees[path.rsplit("/", 1)[1].split("?")[0]]
            items = [
                {"path": p, "sha": s, "mode": "100644", "type": "blob"} for p, s in mapping.items()
            ]
            if self.problem == "mode" and items:
                items[0]["mode"] = "100755"
            if self.problem == "duplicate" and items:
                items.append(items[0])
            return {"tree": items, "truncated": self.problem == "truncated"}
        if path.startswith("/git/blobs/"):
            data = self.blobs[path.rsplit("/", 1)[1]]
            return {
                "encoding": "utf8" if self.problem == "encoding" else "base64",
                "size": len(data) + int(self.problem == "size"),
                "content": base64.encodebytes(data).decode(),
            }
        raise AssertionError(path)

    def write(self, path, payload):
        self.private()
        self.writes.append((path, deepcopy(payload)))
        if path == "/git/blobs":
            data = base64.b64decode(payload["content"])
            sha = batch._blob_sha(data)
            self.blobs[sha] = data
            return {"sha": "f" * 40 if self.problem == "wrong_blob" else sha}
        if path == "/git/trees":
            mapping = dict(self.trees[payload["base_tree"]])
            mapping.update({v["path"]: v["sha"] for v in payload["tree"]})
            sha = digest(canonical(mapping))[:40]
            self.trees[sha] = mapping
            return {"sha": sha}
        if path == "/git/commits":
            sha = digest(canonical(payload))[:40]
            self.commits[sha] = {
                "tree": {"sha": payload["tree"]},
                "parents": [
                    {"sha": p, "url": "invented", "html_url": "invented"}
                    for p in payload["parents"]
                ],
            }
            return {"sha": sha}
        if path == "/git/refs":
            branch = payload["ref"].removeprefix("refs/heads/")
            if branch in self.refs:
                raise DemandError("GITHUB_REQUEST_STOPPED", "scope")
            self.refs[branch] = payload["sha"]
            return {"object": {"sha": "f" * 40 if self.problem == "wrong_ref" else payload["sha"]}}
        raise AssertionError(path)


class MarketFixture:
    def __init__(self):
        self.requests, self.reads = [], []
        self.problem = None
        self.count = None

    def open(self, request, timeout):
        assert timeout == 20
        spec, product = next(
            (s, p) for s in SLICES for p in s.products if s.url(p) == request.full_url
        )
        self.requests.append((spec.capture_id, product))
        if self.problem == "http":
            raise HTTPError(request.full_url, 403, "DO_NOT_PRINT", {}, io.BytesIO(b"PRIVATE"))
        if self.problem == "transport":
            raise URLError("DO_NOT_PRINT")
        body = canonical(provider(spec, product, self.count))
        if self.problem == "key":
            body = body.replace(b"invented_request", b"fixture_provider_key")
        if self.problem == "escaped_key":
            body = body.replace(b"invented_request", b"fixture_provider_\\u006bey")
        if self.problem == "token":
            body = body.replace(b"invented_request", b"fixture_github_key")
        if self.problem == "size":
            body = b" " * (batch.FILE_LIMIT + 1)
        outer = self

        class Response:
            status = 429 if outer.problem == "status" else 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size):
                outer.reads.append(size)
                return body[:size]

        return Response()


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    for k, v in {
        "GITHUB_ACTIONS": "true",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "GITHUB_REPOSITORY": batch.REPOSITORY,
        "GITHUB_ACTOR": "dczhhh",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": batch.EXECUTION_REF,
        "GITHUB_SHA": "a" * 40,
        "MASSIVE_SAMPLES_REVIEWED_SHA": "a" * 40,
        "MASSIVE_SAMPLES_ENABLED": "true",
        "QUANT_SAMPLE_ROOT": str(root),
        "MASSIVE_API_KEY": "fixture_provider_key",
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "1",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(batch, "_ROOT_BINDING", None)
    monkeypatch.setattr(batch, "now", lambda: AS_OF)
    monkeypatch.setattr(
        batch, "_root_details", lambda p: {"path": str(p), "inode": p.stat().st_ino}
    )
    monkeypatch.setattr(batch.time, "sleep", lambda _: None)
    github, market = GitFixture(), MarketFixture()
    monkeypatch.setattr(batch, "GitHub", lambda: github)
    monkeypatch.setattr(batch, "build_opener", lambda *_: market)
    return root, github, market


def fresh(runtime, tmp_path, monkeypatch):
    root = tmp_path / ("runner_" + str(len(list(tmp_path.iterdir()))))
    root.mkdir(mode=0o700)
    monkeypatch.setenv("QUANT_SAMPLE_ROOT", str(root))
    monkeypatch.setattr(batch, "_ROOT_BINDING", None)
    return root


def capture_publish(runtime, spec):
    assert batch.main(["preflight", spec.capture_id]) == 0
    assert batch.main(["capture", spec.capture_id]) == 0
    assert batch.main(["publish", spec.capture_id]) == 0


def test_all_seven_realistic_git_dag_original_binding_and_new_vm_zero_requests(
    runtime, tmp_path, monkeypatch, capsys
):
    _root, github, market = runtime
    for spec in SLICES:
        fresh(runtime, tmp_path, monkeypatch)
        capture_publish(runtime, spec)
        assert batch.main(["publish", spec.capture_id]) == 0
    assert len(market.requests) == 12 and market.reads == [batch.FILE_LIMIT + 1] * 12
    assert sum("samples-v1-slot" in b for b in github.refs) == 12
    assert sum(len(batch.data_scope(s).names) for s in SLICES) == 57
    fresh(runtime, tmp_path, monkeypatch)
    assert batch.main(["analyze"]) == 0
    report = batch.read_files(github, batch.report_scope())[1]["report.json"]
    decoded = json.loads(report)
    assert decoded["structural_and_case_checks_passed"] is True
    assert decoded["cases"]["AAPL_DST"]["one_hour_shift"] is True
    assert decoded["cases"]["SPY_early_close"]["expected_minutes"] == 210
    assert decoded["engine_invoked"] is False and all(decoded[f] is False for f in batch.FLAGS)
    refs = dict(github.refs)
    monkeypatch.delenv("MASSIVE_API_KEY")
    for spec in SLICES:
        fresh(runtime, tmp_path, monkeypatch)
        capture_publish(runtime, spec)
    assert github.refs == refs and len(market.requests) == 12
    assert batch.main(["analyze"]) == 0
    output = capsys.readouterr().out
    assert "fixture_" not in output and str(_root) not in output and "100.5" not in output


def test_source_product_survives_partial_failure_and_fresh_vm_resumes_only_missing_product(
    runtime, tmp_path, monkeypatch
):
    root, github, market = runtime
    spec = SLICES[0]
    batch.guarded_root()
    batch.capture_product(root, github, spec, "raw")
    market.problem = "http"
    assert batch.main(["capture", spec.capture_id]) == 2
    assert batch.main(["capture", spec.capture_id]) == 2
    assert len(market.requests) == 2 and spec.source_branch("raw") in github.refs
    market.problem = None
    fresh(runtime, tmp_path, monkeypatch)
    capture_publish(runtime, spec)
    assert market.requests == [
        (spec.capture_id, "raw"),
        (spec.capture_id, "comparison"),
        (spec.capture_id, "comparison"),
    ]
    assert sum("samples-v1-slot" in b for b in github.refs) == 3


@pytest.mark.parametrize(
    "problem",
    [
        "http",
        "transport",
        "status",
        "size",
        "key",
        "escaped_key",
        "token",
        "wrong_blob",
        "wrong_ref",
        "public",
    ],
)
def test_request_and_archive_failures_do_not_retry_leak_or_publish(runtime, capsys, problem):
    _root, github, market = runtime
    if problem in {"wrong_blob", "wrong_ref", "public"}:
        github.problem = problem
    else:
        market.problem = problem
    assert batch.main(["capture", SLICES[0].capture_id]) == 2
    output = capsys.readouterr().out
    assert (
        "DO_NOT_PRINT" not in output
        and "fixture_" not in output
        and "PRIVATE" not in json.loads(output)["status"]
    )
    assert SLICES[0].data_branch not in github.refs and len(market.requests) <= 1


@pytest.mark.parametrize(
    "key,value",
    [
        ("GITHUB_ACTIONS", "false"),
        ("RUNNER_ENVIRONMENT", "self-hosted"),
        ("GITHUB_REPOSITORY", "wrong/repo"),
        ("GITHUB_ACTOR", "other"),
        ("GITHUB_EVENT_NAME", "pull_request"),
        ("GITHUB_REF", "refs/heads/main"),
        ("GITHUB_SHA", "b" * 40),
        ("MASSIVE_SAMPLES_REVIEWED_SHA", "BAD"),
        ("MASSIVE_SAMPLES_ENABLED", "false"),
        ("QUANT_SAMPLE_ROOT", ""),
        ("QUANT_SAMPLE_ROOT", "\n"),
        ("QUANT_SAMPLE_ROOT", "x" * 4097),
    ],
)
def test_owner_source_root_guards_stop_before_transport(runtime, monkeypatch, key, value):
    _root, github, market = runtime
    monkeypatch.setenv(key, value)
    assert batch.main(["capture", SLICES[0].capture_id]) == 2
    assert market.requests == [] and github.writes == []


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["DO_NOT_PRINT"],
        ["capture", "arbitrary"],
        ["analyze", "DO_NOT_PRINT"],
        ["preflight"],
        ["capture", SLICES[0].capture_id, "DO_NOT_PRINT"],
    ],
)
def test_cli_never_echoes_untrusted_arguments(runtime, capsys, args):
    assert batch.main(args) == 2
    output = capsys.readouterr()
    assert "DO_NOT_PRINT" not in output.out + output.err


def test_per_product_and_global_reservations_are_irreversible(runtime, tmp_path, monkeypatch):
    _root, github, market = runtime
    spec = SLICES[0]
    market.problem = "http"
    for _ in range(2):
        assert batch.main(["capture", spec.capture_id]) == 2
        fresh(runtime, tmp_path, monkeypatch)
    assert batch.main(["capture", spec.capture_id]) == 2 and len(market.requests) == 2
    for slot in range(14):
        github.refs[f"research-reservations/samples-v1-slot-{slot:02d}"] = "a" * 40
    with pytest.raises(DemandError, match="SAMPLE_BATCH_BUDGET_EXHAUSTED"):
        batch.reserve_request(github, SLICES[1], "raw")
    assert len(market.requests) == 2


@pytest.mark.parametrize("problem", ["truncated", "mode", "duplicate", "encoding", "size"])
def test_private_cache_tree_and_blob_tampering_fails_without_market_requests(runtime, problem):
    _root, github, market = runtime
    capture_publish(runtime, SLICES[0])
    github.problem = problem
    assert batch.main(["capture", SLICES[0].capture_id]) == 2
    assert len(market.requests) == 2


def test_source_marker_and_bundle_metadata_tamper_cannot_promote_cache(runtime):
    _root, github, market = runtime
    spec = SLICES[0]
    capture_publish(runtime, spec)
    marker = f"research-reservations/{spec.capture_id}-raw-attempt-0"
    github.refs[marker] = "f" * 40
    assert batch.main(["capture", spec.capture_id]) == 2 and len(market.requests) == 2
    github.refs[marker] = "a" * 40
    _rev, payloads, _commit = batch.read_files(github, batch.data_scope(spec))
    changed = dict(payloads)
    changed["manifest.json"] = changed["manifest.json"].replace(
        b'"raw_verified":false', b'"raw_verified":true'
    )
    assert changed != payloads
    with pytest.raises(DemandError, match="SAMPLE_BUNDLE_INVALID"):
        batch.validate_bundle(github, spec, changed)
    changed = dict(payloads)
    changed["raw.parquet"] += b"invented metadata"
    with pytest.raises(DemandError, match="SAMPLE_BUNDLE_INVALID"):
        batch.validate_bundle(github, spec, changed)


def test_partial_and_missing_slice_audits_are_published_but_never_pass(
    runtime, tmp_path, monkeypatch
):
    _root, github, market = runtime
    market.count = 389
    capture_publish(runtime, SLICES[0])
    fresh(runtime, tmp_path, monkeypatch)
    assert batch.main(["analyze"]) == 2
    report = json.loads(batch.read_files(github, batch.report_scope())[1]["report.json"])
    assert report["slices"][0]["row_counts"] == {"raw": 389, "comparison": 389}
    assert report["structural_and_case_checks_passed"] is False
    assert len(report["inputs"]) == 1 and len(market.requests) == 2


def test_file_scope_conflict_root_rebinding_future_and_report_identity_guards(
    runtime, tmp_path, monkeypatch
):
    root, github, _market = runtime
    spec = SLICES[0]
    batch.guarded_root()
    with pytest.raises(DemandError, match="PRIVATE_ROOT_CHANGED"):
        batch.publish_files(tmp_path, github, batch.source_scope(spec, "raw"), {})
    monkeypatch.setattr(batch, "_root_details", lambda _: {"changed": True})
    with pytest.raises(DemandError, match="PRIVATE_ROOT_CHANGED"):
        batch.guarded_root()
    with pytest.raises(DemandError, match="UNAPPROVED_PRIVATE_DATA_PATH"):
        batch.checked_scope(batch.FileScope("arbitrary", "arbitrary/", ("file",)))
    with pytest.raises(DemandError, match="SAMPLE_FILE_BUDGET_EXCEEDED"):
        batch.check_file_limits(
            batch.source_scope(spec, "raw"), {"response.json": b"", "receipt.json": b"x"}
        )
    with pytest.raises(DemandError, match="SAMPLE_FILE_BUDGET_EXCEEDED"):
        batch.check_file_limits(
            batch.source_scope(spec, "raw"), {"response.json": "text", "receipt.json": b"x"}
        )
    with pytest.raises(DemandError, match="UNAPPROVED_SAMPLE_SLICE"):
        batch.admission(replace(spec, symbol="GME"))
    monkeypatch.setattr(batch, "now", lambda: spec.start_at)
    with pytest.raises(DemandError, match="UNAPPROVED_SAMPLE_SLICE"):
        batch.admission(spec)
    monkeypatch.setenv("GITHUB_RUN_ID", "invalid")
    with pytest.raises(DemandError, match="SAMPLE_RUN_IDENTITY_INVALID"):
        batch.report_scope()


@pytest.mark.parametrize(
    "problem",
    [
        "names",
        "slot",
        "ordinal",
        "parents",
        "parent_sha",
        "future",
        "timezone",
        "false_flag",
        "size",
        "plan",
    ],
)
def test_original_receipt_identity_and_clocks_are_exact_not_coercible(runtime, problem):
    root, github, _market = runtime
    spec = SLICES[0]
    batch.guarded_root()
    batch.capture_product(root, github, spec, "raw")
    _, original, commit = batch.read_files(github, batch.source_scope(spec, "raw"))
    record = json.loads(original["receipt.json"])
    if problem == "names":
        original["unknown"] = b"x"
    if problem == "slot":
        record["global_reservation_slot"] = True
    if problem == "ordinal":
        record["product_attempt"] = 2
    if problem == "parents":
        commit["parents"] = []
    if problem == "parent_sha":
        commit["parents"][0]["sha"] = "f" * 40
    if problem == "future":
        record["observed_at"] = (AS_OF + timedelta(days=1)).isoformat()
    if problem == "timezone":
        record["observed_at"] = AS_OF.replace(tzinfo=None).isoformat()
    if problem == "false_flag":
        record["raw_verified"] = 0
    if problem == "size":
        record["size_bytes"] += 1
    if problem == "plan":
        record["plan_sha256"] = "f" * 64
    original["receipt.json"] = canonical(record)
    with pytest.raises(DemandError, match="SOURCE_SAMPLE_CONFLICT"):
        batch.validate_source(github, spec, "raw", original, commit)


@pytest.mark.parametrize("problem", ["products", "binding", "source_missing"])
def test_bundle_exact_product_and_original_bindings(runtime, problem):
    _root, github, _market = runtime
    spec = SLICES[0]
    capture_publish(runtime, spec)
    _, payloads, _ = batch.read_files(github, batch.data_scope(spec))
    manifest = json.loads(payloads["manifest.json"])
    if problem == "products":
        manifest["source_bindings"].pop("comparison")
    if problem == "binding":
        manifest["source_bindings"]["raw"]["source_commit"] = "f" * 40
    if problem == "source_missing":
        github.refs.pop(spec.source_branch("raw"))
    payloads["manifest.json"] = canonical(manifest)
    with pytest.raises(DemandError):
        batch.validate_bundle(github, spec, payloads)


@pytest.mark.parametrize("problem", ["names", "info", "size"])
def test_upload_manifest_cannot_change_allowlist_or_length(runtime, monkeypatch, problem):
    root, _github, _market = runtime
    spec = SLICES[0]
    assert batch.main(["capture", spec.capture_id]) == 0
    manifest = json.loads(batch.read_checkpoint(root, "upload/manifest.json"))
    if problem == "names":
        manifest["files"].pop("raw.csv")
    if problem == "info":
        manifest["files"]["raw.csv"]["unknown"] = 1
    if problem == "size":
        manifest["files"]["raw.csv"]["size_bytes"] += 1
    original_read = batch.read_checkpoint
    monkeypatch.setattr(
        batch,
        "read_checkpoint",
        lambda r, p: canonical(manifest) if p == "upload/manifest.json" else original_read(r, p),
    )
    assert batch.main(["publish", spec.capture_id]) == 2


def test_create_only_content_conflict_and_missing_commit_stop(runtime):
    root, github, _market = runtime
    spec = SLICES[0]
    capture_publish(runtime, spec)
    _, original, _ = batch.read_files(github, batch.source_scope(spec, "raw"))
    original["receipt.json"] += b" "
    with pytest.raises(DemandError, match="EXISTING_SAMPLE_CONFLICT"):
        batch.publish_files(root, github, batch.source_scope(spec, "raw"), original)
    github.commits.pop("a" * 40)
    with pytest.raises(DemandError, match="GIT_BINDING_INVALID"):
        batch.publish_files(root, github, batch.source_scope(SLICES[1], "raw"), original)
    revision = github.refs[spec.data_branch]
    github.commits.pop(revision)
    with pytest.raises(DemandError, match="GIT_BINDING_INVALID"):
        batch.read_files(github, batch.data_scope(spec))


def test_source_set_and_pagination_without_cursor_never_expand_batch(runtime):
    with pytest.raises(DemandError, match="SAMPLE_PRODUCT_SET_INVALID"):
        batch.bundle_bytes(SLICES[0], {}, {})
    page = provider(SLICES[0], "raw", 1)
    page["next_url"] = (
        SLICES[0]
        .url("raw")
        .replace(
            str(int(SLICES[0].start_at.timestamp() * 1000)),
            str(int(SLICES[0].start_at.timestamp() * 1000) + 60000),
        )
    )
    with pytest.raises(DemandError, match="PAGINATION_UNVERIFIED"):
        contracts.source_rows(SLICES[0], canonical(page), "raw")


def test_product_marker_ambiguity_still_consumes_global_budget(runtime, monkeypatch):
    _root, github, market = runtime
    write = github.write

    def ambiguous(path, payload):
        result = write(path, payload)
        if "-attempt-" in payload.get("ref", ""):
            result["object"]["sha"] = "f" * 40
        return result

    monkeypatch.setattr(github, "write", ambiguous)
    assert batch.main(["capture", SLICES[0].capture_id]) == 2
    assert market.requests == [] and "research-reservations/samples-v1-slot-00" in github.refs
