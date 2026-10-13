"""Owner-authorized private GitHub job intake and bounded private-repo upload.

This separate input channel issues no native archive permit or Engine/data trust.
Real transport is fixed to the approved one-session scope; ordinary CI never uses it.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import math
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, build_opener

import polars as pl

from .archive_contracts import canonical, digest
from .demand import (
    DemandError,
    UntrustedChain,
    check_secrets,
    decode_demand_plan,
    deny,
    fixed_url,
    parse_untrusted_page,
)
from .demand_storage import (
    private_read,
    private_write,
    read_checkpoint,
)
from .massive import NoRedirect
from .private_capture_cli import FLAGS, _root_details, fixed_plan

REPOSITORY = "dczhhh/quant-strategies"
EXECUTION_REF = "refs/heads/research-execution/aapl-v1"
DATA_BRANCH = "research-data/aapl-2026-10-09-v1"
DATA_PATH = "research/data/aapl_2026_10_09_v1/"
_ROOT_BINDING: dict | None = None
COLUMNS = ("timestamp_ms", "open", "high", "low", "close", "volume")
VENDOR_COLUMNS = ("t", "o", "h", "l", "c", "v")
PAYLOADS = (
    "raw.csv",
    "raw.parquet",
    "comparison.csv",
    "comparison.parquet",
    "checks.json",
    "raw_response.json",
    "comparison_response.json",
)
FILES = (*PAYLOADS, "manifest.json")
FILE_LIMIT = 524288
TOTAL_LIMIT = 4194304
SCHEMA = "research_private_repository_bundle_v1"


def now() -> datetime:
    return datetime.now(UTC)


def guarded_root() -> Path:
    global _ROOT_BINDING
    expected = os.environ.get("MASSIVE_ARCHIVE_REVIEWED_SHA", "")
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
        or os.environ.get("GITHUB_ACTOR") != "dczhhh"
        or os.environ.get("GITHUB_EVENT_NAME") not in {"push", "workflow_dispatch"}
        or os.environ.get("GITHUB_REF") != EXECUTION_REF
        or re.fullmatch(r"[0-9a-f]{40}", expected) is None
        or os.environ.get("GITHUB_SHA") != expected
        or os.environ.get("MASSIVE_ARCHIVE_ENABLED") != "true"
    ):
        deny("OWNER_GITHUB_JOB_REQUIRED")
    value = os.environ.get("QUANT_CAPTURE_ROOT", "")
    if not 0 < len(value) <= 4096 or any(ord(c) < 32 for c in value):
        deny("PRIVATE_ROOT_REQUIRED")
    root = Path(value)
    details = _root_details(root)
    if _ROOT_BINDING is None:
        _ROOT_BINDING = details
    elif details != _ROOT_BINDING:
        deny("PRIVATE_ROOT_CHANGED")
    return root


def _key(name: str) -> str:
    key = os.environ.get(name, "")
    if not 0 < len(key) <= 4096 or any(ord(c) <= 32 or ord(c) >= 127 for c in key):
        deny("CREDENTIAL_UNAVAILABLE")
    return key


def _json(content: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                deny("METADATA_INVALID")
            result[key] = value
        return result

    result = json.loads(content, object_pairs_hook=unique)
    if type(result) is not dict:
        deny("METADATA_INVALID")
    return result


class GitHub:
    def __init__(self):
        self._token = _key("GITHUB_TOKEN")

    def request(self, method: str, suffix: str, payload: dict | None = None) -> dict | None:
        request = Request(
            "https://api.github.com/repos/" + REPOSITORY + suffix,
            data=None if payload is None else canonical(payload),
            headers={
                "Authorization": "Bearer " + self._token,
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method=method,
        )
        try:
            with build_opener(NoRedirect()).open(request, timeout=20) as response:
                status, content = response.status, response.read(1048577)
        except HTTPError as error:
            with error:
                if error.code == 404 and method == "GET" and suffix.startswith("/git/ref/"):
                    return None
            deny("PRIVATE_REPOSITORY_REQUEST_STOPPED")
        if status not in {200, 201} or len(content) > 1048576:
            deny("PRIVATE_REPOSITORY_REQUEST_STOPPED")
        return _json(content)

    def private(self) -> None:
        info = self.request("GET", "")
        if (
            info is None
            or info.get("full_name") != REPOSITORY
            or info.get("private") is not True
            or info.get("visibility") != "private"
        ):
            deny("PRIVATE_REPOSITORY_REQUIRED")

    def write(self, suffix: str, payload: dict) -> dict:
        self.private()  # Recheck immediately before every irreversible API write.
        result = self.request("POST", suffix, payload)
        if result is None:
            deny("PRIVATE_REPOSITORY_REQUEST_STOPPED")
        return result

    def reserve_pair(self) -> int:
        # Four immutable, atomic ref reservations survive ephemeral runners. Each
        # reserves two requests and 1 MiB even if a failed job's outcome is unknown.
        revision = _sha(os.environ.get("GITHUB_SHA"))
        for slot in range(4):
            branch = f"research-reservations/aapl-v1-pair-{slot}"
            if self.request("GET", "/git/ref/heads/" + branch) is not None:
                continue
            reserved = self.write("/git/refs", {"ref": "refs/heads/" + branch, "sha": revision})
            if reserved.get("object", {}).get("sha") != revision:
                deny("GIT_BINDING_INVALID")
            return slot
        deny("CAPTURE_BUDGET_EXHAUSTED")


def _rows(rows: list[dict]) -> list[dict]:
    first = 1791552600000
    if len(rows) != 390:
        deny("MINUTE_COVERAGE_INVALID")
    for i, row in enumerate(rows):
        if set(row) != set(COLUMNS) or type(row["timestamp_ms"]) is not int:
            deny("TABLE_SCHEMA_INVALID")
        if row["timestamp_ms"] != first + 60000 * i:
            deny("MINUTE_COVERAGE_INVALID")
        values = [row[c] for c in COLUMNS[1:]]
        if any(type(v) not in (float, int) or not math.isfinite(v) for v in values):
            deny("TABLE_VALUES_INVALID")
        if min(values[:4]) <= 0 or values[4] < 0:
            deny("TABLE_VALUES_INVALID")
        if (
            not row["low"]
            <= min(row["open"], row["close"])
            <= max(row["open"], row["close"])
            <= row["high"]
        ):
            deny("TABLE_VALUES_INVALID")
    return rows


def csv_rows(content: bytes) -> list[dict]:
    if not 0 < len(content) <= FILE_LIMIT:
        deny("FILE_BUDGET_EXCEEDED")
    check_secrets(content)
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    names = reader.fieldnames
    if names is None or len(names) != len(set(names)):
        deny("TABLE_SCHEMA_INVALID")
    if set(names) == set(COLUMNS):
        mapping = COLUMNS
    elif set(VENDOR_COLUMNS) <= set(names) <= set(VENDOR_COLUMNS) | {"vw", "n", "otc"}:
        mapping = VENDOR_COLUMNS
    else:
        deny("TABLE_SCHEMA_INVALID")
    result = []
    for row in reader:
        if None in row or any(row[c] is None for c in mapping) or len(result) >= 390:
            deny("TABLE_SCHEMA_INVALID")
        result.append(
            dict(zip(COLUMNS, [int(row[mapping[0]]), *[float(row[c]) for c in mapping[1:]]]))
        )
    return _rows(result)


def _encode(rows: list[dict]) -> tuple[bytes, bytes]:
    _rows(rows)
    rows = [
        {"timestamp_ms": r["timestamp_ms"], **{c: float(r[c]) for c in COLUMNS[1:]}} for r in rows
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    csv_bytes = output.getvalue().encode()
    frame = pl.DataFrame(csv_rows(csv_bytes)).cast(
        {"timestamp_ms": pl.Int64, **dict.fromkeys(COLUMNS[1:], pl.Float64)}
    )
    buffer = io.BytesIO()
    frame.write_parquet(buffer, compression="zstd", statistics=True)
    return csv_bytes, buffer.getvalue()


def source_rows(content: bytes, product) -> list[dict]:
    if not 0 < len(content) <= FILE_LIMIT:
        deny("FILE_BUDGET_EXCEEDED")
    plan = decode_demand_plan(fixed_plan(), now())
    check_secrets(content)
    original = _json(content)
    if "count" in original:
        # The official stock aggregate fixture includes this optional alias.
        # Validate it before adapting to the unchanged strict native parser;
        # the original response bytes remain the archived/hash-bound source.
        if type(original["count"]) is not int or original["count"] != original.get("resultsCount"):
            deny("PROVIDER_COUNTS_INVALID")
        parser_content = canonical({k: v for k, v in original.items() if k != "count"})
    else:
        parser_content = content
    page = parse_untrusted_page(parser_content, plan, product)
    chain = UntrustedChain(plan, product, fixture=False)
    chain.accept(fixed_url(plan, product), page)
    if chain.next_url is not None:
        deny("PAGINATION_UNVERIFIED")
    if re.fullmatch(r"[A-Za-z0-9_-]{1,256}", page["request_id"]) is None:
        deny("SOURCE_METADATA_INVALID")
    for row in page.get("results", []):
        if "otc" in row and type(row["otc"]) is not bool:
            deny("SOURCE_METADATA_INVALID")
        if "n" in row and (type(row["n"]) is not int or row["n"] < 0):
            deny("SOURCE_METADATA_INVALID")
        if "vw" in row and (
            type(row["vw"]) not in (int, float) or not math.isfinite(row["vw"]) or row["vw"] < 0
        ):
            deny("SOURCE_METADATA_INVALID")
    return _rows(
        [dict(zip(COLUMNS, [r[c] for c in VENDOR_COLUMNS])) for r in page.get("results", [])]
    )


def create_bundle(root: Path, tables: dict, sources: dict) -> None:
    payloads = {}
    for product in ("raw", "comparison"):
        if source_rows(sources[product], product) != tables[product]:
            deny("SOURCE_TABLE_MISMATCH")
        payloads[product + "_response.json"] = sources[product]
        payloads[product + ".csv"], payloads[product + ".parquet"] = _encode(tables[product])
    payloads["checks.json"] = canonical(
        {
            "schema_version": "research_private_intake_checks_v1",
            "row_counts": {"raw": 390, "comparison": 390},
            "minute_coverage_complete": True,
            "product_values_equal": tables["raw"] == tables["comparison"],
            "checks_are_structural_only": True,
            **dict.fromkeys(FLAGS, False),
        }
    )
    manifest = {
        "schema_version": SCHEMA,
        "source_kind": "owner_authorized_http_bytes",
        "source_sha256": {p: digest(body) for p, body in sources.items()},
        "observed_at": now().isoformat(),
        "symbol": "AAPL",
        "plan_sha256": digest(fixed_plan()),
        "request_urls": {
            p: fixed_url(decode_demand_plan(fixed_plan(), now()), p) for p in ("raw", "comparison")
        },
        "clocks": dict.fromkeys(
            ("provider_available_at", "published_at", "historically_known_at"), None
        ),
        "units_and_adjustment_independently_verified": False,
        "license_determination": "not_performed",
        "private_git_history_retention": "owner_authorized_no_automatic_erasure",
        "encoder": "private_repo_intake_v1/polars=" + pl.__version__,
        **dict.fromkeys(FLAGS, False),
        "files": {
            name: {"sha256": digest(data), "size_bytes": len(data)}
            for name, data in payloads.items()
        },
    }
    for name, content in payloads.items():
        private_write(root, "upload/" + name, content)
    private_write(root, "upload/manifest.json", canonical(manifest))


def load_bundle(root: Path) -> dict[str, bytes]:
    manifest_bytes = read_checkpoint(root, "upload/manifest.json")
    manifest = _json(manifest_bytes)
    if (
        manifest.get("schema_version") != SCHEMA
        or manifest.get("plan_sha256") != digest(fixed_plan())
        or manifest.get("source_kind") != "owner_authorized_http_bytes"
        or any(manifest.get(f) is not False for f in FLAGS)
        or manifest.get("clocks")
        != dict.fromkeys(("provider_available_at", "published_at", "historically_known_at"), None)
        or set(manifest.get("files", {})) != set(PAYLOADS)
        or set(manifest.get("source_sha256", {})) != {"raw", "comparison"}
        or any(re.fullmatch(r"[0-9a-f]{64}", v) is None for v in manifest["source_sha256"].values())
    ):
        deny("BUNDLE_INVALID")
    if canonical(manifest) != manifest_bytes:
        deny("BUNDLE_INVALID")
    result = {"manifest.json": manifest_bytes}
    for name in PAYLOADS:
        info = manifest["files"][name]
        if type(info) is not dict or set(info) != {"sha256", "size_bytes"}:
            deny("BUNDLE_INVALID")
        if type(info.get("size_bytes")) is not int or not 0 < info["size_bytes"] <= FILE_LIMIT:
            deny("FILE_BUDGET_EXCEEDED")
        if not 0 < (root / "upload" / name).stat().st_size <= FILE_LIMIT:
            deny("FILE_BUDGET_EXCEEDED")
        data = private_read(root, "upload/" + name, info["sha256"])
        if len(data) != info["size_bytes"]:
            deny("BUNDLE_INVALID")
        result[name] = data
    for product in ("raw", "comparison"):
        source = result[product + "_response.json"]
        if manifest["source_sha256"][product] != digest(source) or source_rows(
            source, product
        ) != csv_rows(result[product + ".csv"]):
            deny("SOURCE_TABLE_MISMATCH")
    # Rebuild from numeric CSVs: arbitrary Parquet metadata/columns cannot be uploaded.
    for product in ("raw", "comparison"):
        csv_bytes, parquet_bytes = _encode(csv_rows(result[product + ".csv"]))
        if result[product + ".csv"] != csv_bytes or result[product + ".parquet"] != parquet_bytes:
            deny("BUNDLE_INVALID")
    expected = {
        "schema_version": "research_private_intake_checks_v1",
        "row_counts": {"raw": 390, "comparison": 390},
        "minute_coverage_complete": True,
        "product_values_equal": result["raw.csv"] == result["comparison.csv"],
        "checks_are_structural_only": True,
        **dict.fromkeys(FLAGS, False),
    }
    if result["checks.json"] != canonical(expected):
        deny("BUNDLE_INVALID")
    # Only a fixed, derived manifest is publishable; reject extra/private metadata.
    expected_keys = {
        "schema_version",
        "source_kind",
        "source_sha256",
        "observed_at",
        "symbol",
        "plan_sha256",
        "request_urls",
        "clocks",
        "units_and_adjustment_independently_verified",
        "license_determination",
        "private_git_history_retention",
        "encoder",
        "files",
        *FLAGS,
    }
    if (
        set(manifest) != expected_keys
        or manifest["symbol"] != "AAPL"
        or manifest["units_and_adjustment_independently_verified"] is not False
        or manifest["license_determination"] != "not_performed"
        or manifest["private_git_history_retention"] != "owner_authorized_no_automatic_erasure"
        or manifest["encoder"] != "private_repo_intake_v1/polars=" + pl.__version__
    ):
        deny("BUNDLE_INVALID")
    stamp = datetime.fromisoformat(manifest["observed_at"])
    if stamp.utcoffset() != now().utcoffset() or stamp > now():
        deny("BUNDLE_INVALID")
    plan = decode_demand_plan(fixed_plan(), now())
    if manifest["request_urls"] != {p: fixed_url(plan, p) for p in ("raw", "comparison")}:
        deny("BUNDLE_INVALID")
    if sum(map(len, result.values())) > TOTAL_LIMIT:
        deny("FILE_BUDGET_EXCEEDED")
    for data in result.values():
        check_secrets(data)
    return result


def capture(root: Path, github: GitHub) -> dict:
    if (root / "upload/manifest.json").exists():
        load_bundle(root)
        return {"cache_reused": True, "market_requests": 0}
    if restore_cache(root, github):
        return {"cache_reused": True, "market_requests": 0}
    if (root / "native/started.json").exists():
        deny("PARTIAL_CAPTURE_REVIEW_REQUIRED")
    github.private()
    plan = decode_demand_plan(fixed_plan(), now())
    key = _key("MASSIVE_API_KEY")
    reservation_slot = github.reserve_pair()
    private_write(
        root,
        "native/started.json",
        canonical({"plan_sha256": digest(fixed_plan()), "max_attempts": 2}),
    )
    tables, sources = {}, {}
    for product in ("raw", "comparison"):
        if guarded_root() != root:
            deny("PRIVATE_ROOT_CHANGED")
        github.private()
        time.sleep(1)
        url = fixed_url(plan, product)
        request = Request(
            url,
            headers={"Accept": "application/json", "Authorization": "Bearer " + key},
            method="GET",
        )
        private_write(
            root, "native/" + product + "_attempt.json", canonical({"url": url, "reserved": True})
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
            deny("FILE_BUDGET_EXCEEDED")
        check_secrets(body, key)
        rows = source_rows(body, product)
        page = _json(body)
        check_secrets(canonical(page), key)
        check_secrets(canonical(page), github._token)
        tables[product] = rows
        sources[product] = body
        private_write(root, "native/" + product + "_response.json", body)
    if guarded_root() != root:
        deny("PRIVATE_ROOT_CHANGED")
    create_bundle(root, tables, sources)
    return {"cache_reused": False, "market_requests": 2, "reservation_slot": reservation_slot}


def _blob_sha(data: bytes) -> str:
    return hashlib.sha1(
        b"blob " + str(len(data)).encode() + b"\0" + data, usedforsecurity=False
    ).hexdigest()


def _sha(value: object) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        deny("GIT_BINDING_INVALID")
    return value


def _existing_tree(github: GitHub, revision: str) -> tuple[str, dict]:
    commit = github.request("GET", "/git/commits/" + _sha(revision))
    if commit is None:
        deny("GIT_BINDING_INVALID")
    tree_sha = _sha(commit["tree"]["sha"])
    tree = github.request("GET", "/git/trees/" + tree_sha + "?recursive=1")
    if tree is None or tree.get("truncated") is not False:
        deny("GIT_BINDING_INVALID")
    scoped = [
        p
        for p in tree["tree"]
        if p["path"].startswith(DATA_PATH) and p["path"] != DATA_PATH.rstrip("/")
    ]
    if any(p["type"] != "blob" or p["mode"] != "100644" for p in scoped):
        deny("GIT_BINDING_INVALID")
    files = {p["path"]: _sha(p["sha"]) for p in scoped}
    if set(files) != {DATA_PATH + name for name in FILES} or len(scoped) != len(files):
        deny("EXISTING_BUNDLE_CONFLICT")
    return tree_sha, files


def restore_cache(root: Path, github: GitHub) -> bool:
    github.private()
    existing = github.request("GET", "/git/ref/heads/" + DATA_BRANCH)
    if existing is None:
        return False
    _, files = _existing_tree(github, existing["object"]["sha"])
    staged = {}
    for name in FILES:
        sha = files[DATA_PATH + name]
        blob = github.request("GET", "/git/blobs/" + sha)
        if blob is None or blob.get("encoding") != "base64":
            deny("GIT_BINDING_INVALID")
        body = base64.b64decode(blob["content"].replace("\n", ""), validate=True)
        if (
            not 0 < len(body) <= FILE_LIMIT
            or blob.get("size") != len(body)
            or _blob_sha(body) != sha
        ):
            deny("GIT_BINDING_INVALID")
        staged[name] = body
    for name in FILES:
        private_write(root, "upload/" + name, staged[name])
    load_bundle(root)
    return True


def publish(root: Path, github: GitHub) -> tuple[str, bool]:
    github.private()
    bundle = load_bundle(root)
    expected = {DATA_PATH + name: _blob_sha(bundle[name]) for name in FILES}
    existing = github.request("GET", "/git/ref/heads/" + DATA_BRANCH)
    parent = _sha(existing["object"]["sha"] if existing else os.environ.get("GITHUB_SHA"))
    if existing:
        _, actual = _existing_tree(github, parent)
        if actual != expected:
            deny("EXISTING_BUNDLE_CONFLICT")
        github.private()
        return parent, True
    commit = github.request("GET", "/git/commits/" + parent)
    if commit is None:
        deny("GIT_BINDING_INVALID")
    tree_sha = _sha(commit["tree"]["sha"])
    for name in FILES:
        data = bundle[name]
        result = github.write(
            "/git/blobs", {"encoding": "base64", "content": base64.b64encode(data).decode()}
        )
        if result.get("sha") != expected[DATA_PATH + name]:
            deny("GIT_BINDING_INVALID")
    entries = [
        {"path": path, "mode": "100644", "type": "blob", "sha": sha}
        for path, sha in expected.items()
    ]
    tree = github.write("/git/trees", {"base_tree": tree_sha, "tree": entries})
    result = github.write(
        "/git/commits",
        {
            "tree": _sha(tree["sha"]),
            "parents": [parent],
            "message": "archive: AAPL 2026-10-09 untrusted research input [skip ci]",
        },
    )
    revision = _sha(result["sha"])
    ref = github.write("/git/refs", {"ref": "refs/heads/" + DATA_BRANCH, "sha": revision})
    if ref.get("object", {}).get("sha") != revision:
        deny("GIT_BINDING_INVALID")
    github.private()
    verified = github.request("GET", "/git/ref/heads/" + DATA_BRANCH)
    if verified is None or verified.get("object", {}).get("sha") != revision:
        deny("GIT_BINDING_INVALID")
    _, actual = _existing_tree(github, revision)
    if actual != expected:
        deny("GIT_BINDING_INVALID")
    return revision, False


def main(argv: list[str] | None = None) -> int:
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument("mode", choices=("preflight", "capture", "publish"))
        args = parser.parse_args(argv)
        root = guarded_root()
        github = GitHub()
        github.private()
        extra = {}
        if args.mode == "capture":
            extra = capture(root, github)
        elif args.mode == "publish":
            revision, cached = publish(root, github)
            extra = {"data_commit": revision, "already_published": cached, "files": len(FILES)}
        result = {
            "schema_version": "research_private_transfer_audit_v1",
            "status": "untrusted_input_only",
            "code": "PRIVATE_OPERATION_COMPLETE",
            "license_determination": "not_performed",
            **dict.fromkeys(FLAGS, False),
            **extra,
        }
        exit_code = 0
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
            "schema_version": "research_private_transfer_audit_v1",
            "status": "blocked",
            "code": error.code
            if isinstance(error, DemandError) and re.fullmatch(r"[A-Z_]{1,64}", error.code)
            else "PRIVATE_TRANSFER_STOPPED",
            **dict.fromkeys(FLAGS, False),
        }
        exit_code = 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
