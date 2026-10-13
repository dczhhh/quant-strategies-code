"""Private, source-pinned seven-slice capture and non-PIT structural analysis.

Valid original products are committed before requesting the next product, so a
fresh runner can resume without redownloading a successful original response.
No native permit, trusted dataset, Engine or strategy-performance token is issued.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import statistics
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, build_opener

import polars as pl

from .archive_contracts import canonical, digest
from .demand import DemandError, check_secrets, deny
from .demand_storage import private_read, private_write, read_checkpoint
from .instrument_policy import admit_instrument
from .massive import NoRedirect
from .private_capture_cli import FLAGS, _root_details
from .private_repo_ingest import FILE_LIMIT, REPOSITORY, GitHub, _blob_sha, _json, _key, _sha
from .sample_contracts import (
    FROZEN_SHA256,
    TOTAL_LIMIT,
    SampleSlice,
    decode_csv,
    encode_rows,
    frozen_batch,
    sample_slice,
    sample_slices,
    security_evidence,
    source_rows,
    structural_checks,
)

EXECUTION_REF = "refs/heads/research-execution/samples-v1"
_ROOT_BINDING: dict | None = None
SOURCE_SCHEMA = "research_sample_original_product_v1"
BUNDLE_SCHEMA = "research_private_sample_bundle_v1"
REPORT_SCHEMA = "research_sample_analysis_v1"
CLOCKS = dict.fromkeys(("provider_available_at", "published_at", "historically_known_at"), None)


def now() -> datetime:
    return datetime.now(UTC)


def guarded_root() -> Path:
    global _ROOT_BINDING
    expected = os.environ.get("MASSIVE_SAMPLES_REVIEWED_SHA", "")
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
        or os.environ.get("GITHUB_ACTOR") != "dczhhh"
        or os.environ.get("GITHUB_EVENT_NAME") not in {"push", "workflow_dispatch"}
        or os.environ.get("GITHUB_REF") != EXECUTION_REF
        or re.fullmatch(r"[0-9a-f]{40}", expected) is None
        or os.environ.get("GITHUB_SHA") != expected
        or os.environ.get("MASSIVE_SAMPLES_ENABLED") != "true"
    ):
        deny("OWNER_SAMPLE_JOB_REQUIRED")
    value = os.environ.get("QUANT_SAMPLE_ROOT", "")
    if not 0 < len(value) <= 4096 or any(ord(c) < 32 for c in value):
        deny("PRIVATE_ROOT_REQUIRED")
    root = Path(value)
    details = _root_details(root)
    if _ROOT_BINDING is None:
        _ROOT_BINDING = details
    elif details != _ROOT_BINDING:
        deny("PRIVATE_ROOT_CHANGED")
    return root


def admission(spec: SampleSlice) -> dict:
    if sample_slice(spec.capture_id) != spec or spec.end_at > now():
        deny("UNAPPROVED_SAMPLE_SLICE")
    return admit_instrument(
        spec.symbol, spec.day, spec.plan_sha256, security_evidence(spec.symbol), now()
    )


@dataclass(frozen=True, slots=True)
class FileScope:
    branch: str
    path: str
    names: tuple[str, ...]


def source_scope(spec: SampleSlice, product: str) -> FileScope:
    return FileScope(
        spec.source_branch(product), spec.source_path(product), ("response.json", "receipt.json")
    )


def data_scope(spec: SampleSlice) -> FileScope:
    names = tuple(
        name for p in spec.products for name in (p + "_response.json", p + ".csv", p + ".parquet")
    )
    return FileScope(
        spec.data_branch, spec.data_path, (*names, "admission.json", "checks.json", "manifest.json")
    )


def report_scope() -> FileScope:
    run, attempt = os.environ.get("GITHUB_RUN_ID", ""), os.environ.get("GITHUB_RUN_ATTEMPT", "")
    if any(re.fullmatch(r"[1-9][0-9]{0,19}", value) is None for value in (run, attempt)):
        deny("SAMPLE_RUN_IDENTITY_INVALID")
    return FileScope(
        f"research-data/samples-v1-audit-{run}-{attempt}",
        f"research/reports/samples_v1/run_{run}_attempt_{attempt}/",
        ("report.json",),
    )


def checked_scope(scope: FileScope) -> None:
    allowed = [data_scope(s) for s in sample_slices()]
    allowed.extend(source_scope(s, p) for s in sample_slices() for p in s.products)
    if scope not in allowed and scope != report_scope():
        deny("UNAPPROVED_PRIVATE_DATA_PATH")


def _ref(github: GitHub, branch: str) -> str | None:
    item = github.request("GET", "/git/ref/heads/" + branch)
    return None if item is None else _sha(item["object"]["sha"])


def _tree(github: GitHub, scope: FileScope, revision: str) -> tuple[dict, dict[str, str]]:
    checked_scope(scope)
    commit = github.request("GET", "/git/commits/" + _sha(revision))
    if commit is None:
        deny("GIT_BINDING_INVALID")
    tree = github.request("GET", "/git/trees/" + _sha(commit["tree"]["sha"]) + "?recursive=1")
    if tree is None or tree.get("truncated") is not False:
        deny("GIT_BINDING_INVALID")
    scoped = [v for v in tree["tree"] if v["path"].startswith(scope.path)]
    if any(v["type"] != "blob" or v["mode"] != "100644" for v in scoped):
        deny("GIT_BINDING_INVALID")
    bound = {v["path"]: _sha(v["sha"]) for v in scoped}
    if set(bound) != {scope.path + name for name in scope.names} or len(scoped) != len(bound):
        deny("EXISTING_SAMPLE_CONFLICT")
    return commit, bound


def read_files(github: GitHub, scope: FileScope) -> tuple[str, dict[str, bytes], dict] | None:
    checked_scope(scope)
    github.private()
    revision = _ref(github, scope.branch)
    if revision is None:
        return None
    commit, mapping = _tree(github, scope, revision)
    output = {}
    for name in scope.names:
        sha = mapping[scope.path + name]
        blob = github.request("GET", "/git/blobs/" + sha)
        if blob is None or blob.get("encoding") != "base64":
            deny("GIT_BINDING_INVALID")
        content = base64.b64decode(blob["content"].replace("\n", ""), validate=True)
        if (
            not 0 < len(content) <= FILE_LIMIT
            or blob.get("size") != len(content)
            or _blob_sha(content) != sha
        ):
            deny("GIT_BINDING_INVALID")
        output[name] = content
    check_file_limits(scope, output)
    return revision, output, commit


def check_file_limits(scope: FileScope, payloads: dict[str, bytes]) -> None:
    checked_scope(scope)
    if (
        set(payloads) != set(scope.names)
        or any(type(v) is not bytes for v in payloads.values())
        or sum(map(len, payloads.values())) > TOTAL_LIMIT
    ):
        deny("SAMPLE_FILE_BUDGET_EXCEEDED")
    for content in payloads.values():
        if type(content) is not bytes or not 0 < len(content) <= FILE_LIMIT:
            deny("SAMPLE_FILE_BUDGET_EXCEEDED")
        check_secrets(content)


def publish_files(
    root: Path, github: GitHub, scope: FileScope, payloads: dict[str, bytes]
) -> tuple[str, bool]:
    if guarded_root() != root:
        deny("PRIVATE_ROOT_CHANGED")
    check_file_limits(scope, payloads)
    github.private()
    expected = {scope.path + name: _blob_sha(content) for name, content in payloads.items()}
    current = _ref(github, scope.branch)
    if current is not None:
        _, actual = _tree(github, scope, current)
        if actual != expected:
            deny("EXISTING_SAMPLE_CONFLICT")
        return current, True
    parent = _sha(os.environ.get("GITHUB_SHA"))
    commit = github.request("GET", "/git/commits/" + parent)
    if commit is None:
        deny("GIT_BINDING_INVALID")
    for path, sha in expected.items():
        content = payloads[path.removeprefix(scope.path)]
        result = github.write(
            "/git/blobs", {"encoding": "base64", "content": base64.b64encode(content).decode()}
        )
        if result.get("sha") != sha:
            deny("GIT_BINDING_INVALID")
    tree = github.write(
        "/git/trees",
        {
            "base_tree": _sha(commit["tree"]["sha"]),
            "tree": [
                {"path": p, "mode": "100644", "type": "blob", "sha": s} for p, s in expected.items()
            ],
        },
    )
    result = github.write(
        "/git/commits",
        {
            "tree": _sha(tree["sha"]),
            "parents": [parent],
            "message": "archive: bounded untrusted sample [skip ci]",
        },
    )
    revision = _sha(result["sha"])
    ref = github.write("/git/refs", {"ref": "refs/heads/" + scope.branch, "sha": revision})
    if ref.get("object", {}).get("sha") != revision:
        deny("GIT_BINDING_INVALID")
    github.private()
    if _ref(github, scope.branch) != revision or _tree(github, scope, revision)[1] != expected:
        deny("GIT_BINDING_INVALID")
    return revision, False


def reserve_request(github: GitHub, spec: SampleSlice, product: str) -> tuple[int, int]:
    spec.check_product(product)
    admission(spec)
    revision = _sha(os.environ.get("GITHUB_SHA"))
    prefix = "research-reservations/" + spec.capture_id + "-" + product + "-attempt-"
    ordinal = next((i for i in range(2) if _ref(github, prefix + str(i)) is None), None)
    if ordinal is None:
        deny("SAMPLE_PRODUCT_BUDGET_EXHAUSTED")
    for slot in range(frozen_batch()["max_market_attempts"]):
        branch = f"research-reservations/samples-v1-slot-{slot:02d}"
        if _ref(github, branch) is not None:
            continue
        result = github.write("/git/refs", {"ref": "refs/heads/" + branch, "sha": revision})
        if result.get("object", {}).get("sha") != revision:
            deny("GIT_BINDING_INVALID")
        marker = github.write(
            "/git/refs", {"ref": "refs/heads/" + prefix + str(ordinal), "sha": revision}
        )
        if marker.get("object", {}).get("sha") != revision:
            deny("GIT_BINDING_INVALID")
        return slot, ordinal
    deny("SAMPLE_BATCH_BUDGET_EXHAUSTED")


def _source_record(
    spec: SampleSlice, product: str, content: bytes, admitted: dict, slot: int, ordinal: int
) -> dict:
    return {
        "schema_version": SOURCE_SCHEMA,
        "source_kind": "owner_authorized_http_bytes",
        "capture_id": spec.capture_id,
        "product": product,
        "plan_sha256": spec.plan_sha256,
        "batch_sha256": FROZEN_SHA256,
        "admission_sha256": digest(canonical(admitted)),
        "request_url": spec.url(product),
        "source_sha256": digest(content),
        "size_bytes": len(content),
        "source_revision": _sha(os.environ.get("GITHUB_SHA")),
        "global_reservation_slot": slot,
        "product_attempt": ordinal,
        "observed_at": now().isoformat(),
        "clocks": CLOCKS,
        **dict.fromkeys(FLAGS, False),
    }


def validate_source(
    github: GitHub, spec: SampleSlice, product: str, original: dict[str, bytes], commit: dict
) -> tuple[list[dict], dict]:
    admitted = admission(spec)
    if set(original) != {"response.json", "receipt.json"}:
        deny("SOURCE_SAMPLE_CONFLICT")
    body, record = original["response.json"], _json(original["receipt.json"])
    slot, ordinal = record["global_reservation_slot"], record["product_attempt"]
    if (
        type(slot) is not int
        or not 0 <= slot < 14
        or type(ordinal) is not int
        or not 0 <= ordinal < 2
        or type(commit.get("parents")) is not list
        or len(commit["parents"]) != 1
        or commit["parents"][0].get("sha") != record["source_revision"]
    ):
        deny("SOURCE_SAMPLE_CONFLICT")
    revision = _sha(record["source_revision"])
    if (
        _ref(github, f"research-reservations/samples-v1-slot-{slot:02d}") != revision
        or _ref(github, f"research-reservations/{spec.capture_id}-{product}-attempt-{ordinal}")
        != revision
    ):
        deny("SOURCE_RESERVATION_CONFLICT")
    stamp = datetime.fromisoformat(record["observed_at"])
    if stamp.utcoffset() != now().utcoffset() or stamp > now():
        deny("SOURCE_SAMPLE_CONFLICT")
    expected = _source_record(spec, product, body, admitted, slot, ordinal)
    expected.update(source_revision=revision, observed_at=record["observed_at"])
    if original["receipt.json"] != canonical(expected):
        deny("SOURCE_SAMPLE_CONFLICT")
    check_secrets(canonical(record), github._token)
    return source_rows(spec, body, product), record


def capture_product(
    root: Path, github: GitHub, spec: SampleSlice, product: str
) -> tuple[bytes, dict, str, bool]:
    admitted = admission(spec)
    scope = source_scope(spec, product)
    existing = read_files(github, scope)
    if existing is not None:
        revision, payloads, commit = existing
        _, record = validate_source(github, spec, product, payloads, commit)
        return payloads["response.json"], record, revision, True
    started = product + "_started.json"
    if (root / "native" / started).exists():
        deny("PARTIAL_SAMPLE_REVIEW_REQUIRED")
    key = _key("MASSIVE_API_KEY")
    slot, ordinal = reserve_request(github, spec, product)
    if guarded_root() != root:
        deny("PRIVATE_ROOT_CHANGED")
    github.private()
    private_write(root, "native/" + started, canonical({"slot": slot, "product_attempt": ordinal}))
    time.sleep(1)
    request = Request(
        spec.url(product),
        headers={"Accept": "application/json", "Authorization": "Bearer " + key},
        method="GET",
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=20) as response:
            if response.status != 200:
                deny("MARKET_REQUEST_STOPPED")
            body = response.read(FILE_LIMIT + 1)
    except HTTPError as error:
        error.close()
        deny("MARKET_REQUEST_STOPPED")
    if len(body) > FILE_LIMIT:
        deny("SAMPLE_RESPONSE_BUDGET_EXCEEDED")
    check_secrets(body, key)
    decoded = canonical(_json(body))
    check_secrets(decoded, key)
    check_secrets(decoded, github._token)
    source_rows(spec, body, product)
    record = _source_record(spec, product, body, admitted, slot, ordinal)
    original = {"response.json": body, "receipt.json": canonical(record)}
    for name, content in original.items():
        private_write(root, "native/" + product + "/" + name, content)
    revision, _ = publish_files(root, github, scope, original)
    return body, record, revision, False


def bundle_bytes(spec: SampleSlice, sources: dict[str, bytes], bindings: dict) -> dict[str, bytes]:
    admitted = admission(spec)
    tables, payloads = {}, {}
    if set(sources) != set(spec.products) or set(bindings) != set(spec.products):
        deny("SAMPLE_PRODUCT_SET_INVALID")
    for product in spec.products:
        tables[product] = source_rows(spec, sources[product], product)
        payloads[product + "_response.json"] = sources[product]
        payloads[product + ".csv"], payloads[product + ".parquet"] = encode_rows(
            spec, tables[product]
        )
    payloads["admission.json"] = canonical(admitted)
    payloads["checks.json"] = canonical(
        {**structural_checks(spec, tables), **dict.fromkeys(FLAGS, False)}
    )
    manifest = {
        "schema_version": BUNDLE_SCHEMA,
        "source_kind": "owner_authorized_http_bytes",
        "slice": spec.wire(),
        "source_bindings": bindings,
        "clocks": CLOCKS,
        "adjustment_anchor_verified": False,
        "units_independently_verified": False,
        "license_determination": "not_performed",
        "encoder": "private_sample_batch_v1/polars=" + pl.__version__,
        **dict.fromkeys(FLAGS, False),
        "files": {
            name: {"sha256": digest(content), "size_bytes": len(content)}
            for name, content in payloads.items()
        },
    }
    payloads["manifest.json"] = canonical(manifest)
    check_file_limits(data_scope(spec), payloads)
    return payloads


def validate_bundle(github: GitHub, spec: SampleSlice, payloads: dict[str, bytes]) -> dict:
    admission(spec)
    check_file_limits(data_scope(spec), payloads)
    manifest = _json(payloads["manifest.json"])
    sources, bindings = {}, {}
    if set(manifest["source_bindings"]) != set(spec.products):
        deny("SAMPLE_PRODUCT_SET_INVALID")
    for product in spec.products:
        existing = read_files(github, source_scope(spec, product))
        if existing is None:
            deny("SOURCE_SAMPLE_CONFLICT")
        revision, original, commit = existing
        _, record = validate_source(github, spec, product, original, commit)
        sources[product] = original["response.json"]
        bindings[product] = {
            "source_commit": revision,
            "receipt_sha256": digest(original["receipt.json"]),
            "response_sha256": record["source_sha256"],
        }
        if manifest["source_bindings"][product] != bindings[product]:
            deny("SOURCE_SAMPLE_CONFLICT")
    if payloads != bundle_bytes(spec, sources, bindings):
        deny("SAMPLE_BUNDLE_INVALID")
    return _json(payloads["checks.json"])


def capture(root: Path, github: GitHub, spec: SampleSlice) -> dict:
    admission(spec)
    cached = read_files(github, data_scope(spec))
    if cached is not None:
        _, payloads, _ = cached
        checks = validate_bundle(github, spec, payloads)
        for name, content in payloads.items():
            private_write(root, "upload/" + name, content)
        return {
            "cache_reused": True,
            "market_requests": 0,
            "minute_coverage_complete": checks["minute_coverage_complete"],
        }
    sources, bindings, requests = {}, {}, 0
    for product in spec.products:
        body, record, revision, reused = capture_product(root, github, spec, product)
        requests += not reused
        sources[product] = body
        bindings[product] = {
            "source_commit": revision,
            "receipt_sha256": digest(canonical(record)),
            "response_sha256": digest(body),
        }
    payloads = bundle_bytes(spec, sources, bindings)
    for name, content in payloads.items():
        private_write(root, "upload/" + name, content)
    return {
        "cache_reused": requests == 0,
        "market_requests": requests,
        "minute_coverage_complete": _json(payloads["checks.json"])["minute_coverage_complete"],
    }


def publish(root: Path, github: GitHub, spec: SampleSlice) -> dict:
    scope = data_scope(spec)
    manifest = _json(read_checkpoint(root, "upload/manifest.json"))
    if set(manifest["files"]) != set(scope.names) - {"manifest.json"}:
        deny("SAMPLE_BUNDLE_INVALID")
    payloads = {"manifest.json": read_checkpoint(root, "upload/manifest.json")}
    for name, info in manifest["files"].items():
        if type(info) is not dict or set(info) != {"sha256", "size_bytes"}:
            deny("SAMPLE_BUNDLE_INVALID")
        payloads[name] = private_read(root, "upload/" + name, info["sha256"])
        if type(info["size_bytes"]) is not int or len(payloads[name]) != info["size_bytes"]:
            deny("SAMPLE_BUNDLE_INVALID")
    checks = validate_bundle(github, spec, payloads)
    revision, reused = publish_files(root, github, scope, payloads)
    return {
        "data_commit": revision,
        "already_published": reused,
        "files": len(payloads),
        "row_counts": checks["row_counts"],
        "minute_coverage_complete": checks["minute_coverage_complete"],
    }


def _factor(tables: dict[str, list[dict]]) -> float | None:
    raw = tables["raw"]
    comparison = {r["timestamp_ms"]: r for r in tables["comparison"]}
    ratios = [
        r[c] / comparison[r["timestamp_ms"]][c]
        for r in raw
        if r["timestamp_ms"] in comparison
        for c in ("open", "high", "low", "close")
    ]
    return (
        statistics.median(ratios)
        if ratios and all(math.isfinite(v) and v > 0 for v in ratios)
        else None
    )


def analyze(root: Path, github: GitHub) -> dict:
    inputs, rows, cases = {}, {}, {}
    reports = []
    for spec in sample_slices():
        admission(spec)
        item = read_files(github, data_scope(spec))
        if item is None:
            reports.append({"capture_id": spec.capture_id, "status": "not_captured_or_failed"})
            continue
        revision, payloads, _ = item
        checks = validate_bundle(github, spec, payloads)
        inputs[spec.capture_id] = {
            "data_commit": revision,
            "manifest_sha256": digest(payloads["manifest.json"]),
        }
        rows[spec.capture_id] = {p: decode_csv(spec, payloads[p + ".csv"]) for p in spec.products}
        reports.append(
            {
                "capture_id": spec.capture_id,
                "symbol": spec.symbol,
                "day": spec.day.isoformat(),
                "start_at": spec.start_at.isoformat(),
                "end_at": spec.end_at.isoformat(),
                "expected_minutes": spec.expected_minutes,
                **checks,
            }
        )
    for fact in frozen_batch()["issuer_facts"]:
        pair = [s for s in sample_slices() if s.symbol == fact["symbol"]]
        factors = [_factor(rows[s.capture_id]) if s.capture_id in rows else None for s in pair]
        expected = fact["new_shares"] / fact["old_shares"]
        before, after = factors
        relative = (
            before / after if before is not None and after is not None and after > 0 else None
        )
        cases[fact["symbol"] + "_split"] = {
            "issuer_fact": fact,
            "observed_relative_factor": relative,
            "price_scale_matches_issuer_summary": relative is not None
            and math.isclose(relative, expected, rel_tol=0.001),
            "independent_volume_policy_verified": False,
        }
    aapl = [s for s in sample_slices() if s.symbol == "AAPL"]
    half = next(s for s in sample_slices() if s.symbol == "SPY")
    cases["AAPL_DST"] = {
        "utc_open_hours": [s.start_at.hour for s in aapl],
        "one_hour_shift": aapl[0].start_at.hour - aapl[1].start_at.hour == 1,
    }
    cases["SPY_early_close"] = {
        "expected_minutes": half.expected_minutes,
        "session_is_210_minutes": half.expected_minutes == 210,
    }
    complete = len(inputs) == 7 and all(r.get("minute_coverage_complete") is True for r in reports)
    cases_ok = all(
        c["price_scale_matches_issuer_summary"] for k, c in cases.items() if k.endswith("_split")
    )
    report = {
        "schema_version": REPORT_SCHEMA,
        "batch_sha256": FROZEN_SHA256,
        "inputs": inputs,
        "slices": reports,
        "cases": cases,
        "structural_and_case_checks_passed": complete and cases_ok,
        "source_metadata_is_retrospective": True,
        "historical_availability_unknown": True,
        "engine_invoked": False,
        "strategy_performance_computed": False,
        "analysis_kind": "untrusted_non_PIT_structural_study",
        **dict.fromkeys(FLAGS, False),
    }
    content = canonical(report)
    if len(content) > 65536:
        deny("SAMPLE_REPORT_BUDGET_EXCEEDED")
    revision, reused = publish_files(root, github, report_scope(), {"report.json": content})
    return {
        "report_commit": revision,
        "already_published": reused,
        "slices": len(inputs),
        "structural_and_case_checks_passed": report["structural_and_case_checks_passed"],
        "market_requests": 0,
    }


class _SafeParser(argparse.ArgumentParser):
    def error(self, message):
        deny("SAMPLE_COMMAND_INVALID")


def main(argv: list[str] | None = None) -> int:
    try:
        parser = _SafeParser()
        parser.add_argument("mode", choices=("preflight", "capture", "publish", "analyze"))
        parser.add_argument("slice_id", nargs="?")
        args = parser.parse_args(argv)
        root = guarded_root()
        if args.mode == "analyze":
            if args.slice_id is not None:
                deny("SAMPLE_COMMAND_INVALID")
            spec = None
        else:
            spec = sample_slice(args.slice_id)
            admission(spec)
        github = GitHub()
        github.private()
        extra = {}
        if args.mode == "analyze":
            extra = analyze(root, github)
        elif args.mode == "capture" and spec is not None:
            extra = capture(root, github, spec)
        elif args.mode == "publish" and spec is not None:
            extra = publish(root, github, spec)
        result = {
            "schema_version": "research_private_sample_audit_v1",
            "status": "untrusted_input_only",
            "code": "PRIVATE_SAMPLE_OPERATION_COMPLETE",
            **dict.fromkeys(FLAGS, False),
            **extra,
        }
        exit_code = 2 if extra.get("structural_and_case_checks_passed") is False else 0
    except (
        ValueError,
        TypeError,
        KeyError,
        OSError,
        AttributeError,
        RecursionError,
        OverflowError,
    ) as error:
        result = {
            "schema_version": "research_private_sample_audit_v1",
            "status": "blocked",
            "code": error.code
            if isinstance(error, DemandError) and re.fullmatch(r"[A-Z_]{1,64}", error.code)
            else "PRIVATE_SAMPLE_OPERATION_STOPPED",
            **dict.fromkeys(FLAGS, False),
        }
        exit_code = 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
