"""Frozen four-year gaps: single-attempt raw capture after source qualification."""

from __future__ import annotations

import base64
import gzip
import io
import json
import os
import subprocess
import sys
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import rotation_four_year_inventory as s  # noqa: E402

c, u = s.c, s.u
TICKERS = s.s.TICKERS
INVENTORY_HASH = "8a35353e32f2415a361823cd85971f41ff9b5c8c25fa12d3811835a57a2cc6f0"
INVENTORY_SOURCE = "9f31bdde3fb567970b498402fd7bcb4c6e81e0b7"
INVENTORY_COMMIT = "7279fdc6096db6ba273716c31c0790ea58d2b570"
WORKFLOW = ".github/workflows/rotation-four-year-capture.yml"
PREFIX = "research/data/rotation_four_year_gap_v1/"
GROUP_SIZE, MAX_PARTS = 32, 36
MAX_EXPANSION = GROUP_SIZE * (c.m.FILE_LIMIT * 2 + 8192)
POLICY = {
    "schema": "rotation_four_year_gap_capture_v1",
    "inventory_bundle_sha256": INVENTORY_HASH,
    "inventory_source": INVENTORY_SOURCE,
    "inventory_policy_sha256": s.POLICY_HASH,
    "maximum_vendor_requests": 2304,
    "maximum_vendor_requests_per_asset": 192,
    "maximum_archive_content_writes": 3168,
    "maximum_response_bytes": c.m.FILE_LIMIT,
    "maximum_products_per_group": GROUP_SIZE,
    "maximum_compressed_parts_per_group": MAX_PARTS,
    "single_attempt_per_reservation": True,
    "automatic_retry_allowed": False,
    "new_official_requests": 0,
    "redirects_allowed": False,
    "pagination_allowed": False,
    "unknown_reservation_reset_allowed": False,
    "engine_invoked": False,
    "PIT_verified": False,
    "performance_admission": False,
}


@lru_cache(maxsize=12)
def products(ticker):
    s.need(ticker in TICKERS, "CAPTURE_TICKER_SCOPE")
    rows = []
    for kind, spans in (
        ("daily", (("2023-01-03", "2023-10-31"), ("2025-11-06", "2025-12-31"))),
        (
            "minute",
            (
                ("2023-01-03", "2024-11-29"),
                ("2025-07-01", "2025-08-28"),
                ("2025-11-06", "2025-12-31"),
            ),
        ),
        ("dividends", (("2023-01-03", "2023-10-31"), ("2025-11-06", "2025-12-31"))),
    ):
        for first, last in spans:
            days = c.sessions(date.fromisoformat(first), date.fromisoformat(last))
            size = 3 if kind == "minute" else len(days)
            rows += [
                s.product(ticker, kind, days[i], days[min(i + size - 1, len(days) - 1)])
                for i in range(0, len(days), size)
            ]
    s.need(len(rows) == 192, "CAPTURE_PLAN_CHANGED")
    return tuple(rows)


PLAN_HASH = c.m.sha256(
    u.canonical({"policy": POLICY, "products": {t: products(t) for t in TICKERS}})
)


@lru_cache(maxsize=12)
def groups(ticker):
    rows = products(ticker)
    return tuple(rows[i : i + GROUP_SIZE] for i in range(0, len(rows), GROUP_SIZE))


def reference(ticker, index, reservation=False):
    c.p.need(type(index) is int and 0 <= index < len(groups(ticker)), "FOUR_YEAR_GAP_GROUP_INVALID")
    return (
        "research-reservations/" if reservation else "research-data/"
    ) + f"rotation-four-year-gap-v1-{ticker.lower()}-{index}"


def reservation(ticker, index, source):
    return u.canonical(
        {
            "schema": "rotation_four_year_gap_reservation_v1",
            "source_commit": c.m.sha(source),
            "plan_sha256": PLAN_HASH,
            "products": groups(ticker)[index],
            "policy": POLICY,
        }
    )


def bounded_gunzip(body, limit):
    with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
        value = stream.read(limit + 1)
        c.p.need(len(value) <= limit, "COMPRESSED_ARCHIVE_EXPANSION_LIMIT")
        return value


def check_inventory(archive):
    s.need(archive.ref(s.REPORT_REF) == INVENTORY_COMMIT, "CAPTURE_INVENTORY_REF_PIN")
    names = {s.PREFIX + name for name in ("inventory.json.gz", "manifest.json")}
    saved, parent = c.b.read(archive, s.REPORT_REF, names)
    c.p.need(
        parent == c.m.BOOTSTRAP
        and c.m.sha256(u.canonical({k: c.m.sha256(v) for k, v in sorted(saved.items())}))
        == INVENTORY_HASH,
        "FIXED_INVENTORY_HASH_CONFLICT",
    )
    manifest = c.b.decode(saved[s.PREFIX + "manifest.json"])
    c.p.need(
        manifest["source_commit"] == INVENTORY_SOURCE
        and manifest["schema"] == "rotation_four_year_inventory_manifest_v1"
        and manifest["policy_sha256"] == s.POLICY_HASH
        and manifest["files"]
        == {s.PREFIX + "inventory.json.gz": c.m.sha256(saved[s.PREFIX + "inventory.json.gz"])},
        "INVENTORY_MANIFEST_CONFLICT",
    )
    raw = bounded_gunzip(saved[s.PREFIX + "inventory.json.gz"], 8 * 1024 * 1024)
    s.need(c.m.sha256(raw) == manifest["uncompressed_sha256"], "CAPTURE_INVENTORY_LOGICAL_HASH")
    report = c.b.decode(raw)
    proposed = report["proposed_products_not_executed"]
    expected = [p for t in TICKERS for p in products(t)]
    c.p.need(
        report["schema"] == "rotation_four_year_inventory_v1"
        and report["policy"] == json.loads(u.canonical(s.POLICY))
        and report["policy_sha256"] == s.POLICY_HASH
        and report["summary"]["proposed_registered_scope_requests"] == 2304
        and report["summary"]["any_requested_but_unrepresented_rows"] is False
        and proposed == expected
        and report["summary"]["new_vendor_requests"] == 0,
        "FIXED_INVENTORY_PLAN_CONFLICT",
    )


def fetch(product, key):
    c.p.need(
        any(product in products(t) for t in TICKERS), "FOUR_YEAR_GAP_REQUEST_OUTSIDE_FIXED_PLAN"
    )
    c.p.need(
        type(key) is str and 0 < len(key) <= 4096 and not any(v.isspace() for v in key),
        "CREDENTIAL_UNAVAILABLE",
    )
    request = Request(
        product["url"], headers={"Authorization": "Bearer " + key, "Accept": "application/json"}
    )
    try:
        with build_opener(c.m.NoRedirect()).open(request, timeout=45) as response:
            status, body = response.status, response.read(c.m.FILE_LIMIT + 1)
    except HTTPError as error:
        with error:
            status, body = error.code, error.read(c.m.FILE_LIMIT + 1)
    c.b.secret_free(body, (key,))
    return status, body


def read_group(archive, ticker, index, *, source=None):
    name = reference(ticker, index)
    revision = archive.ref(name)
    c.p.need(revision is not None, "FOUR_YEAR_GAP_CAPTURE_GROUP_MISSING")
    commit = archive.request("GET", "/git/commits/" + revision)
    c.p.need(
        commit is not None and len(commit["parents"]) == 1, "FOUR_YEAR_GAP_CAPTURE_PARENT_INVALID"
    )
    tree = archive.request("GET", "/git/trees/" + commit["tree"]["sha"] + "?recursive=1")
    c.p.need(
        tree is not None and tree.get("truncated") is False, "FOUR_YEAR_GAP_CAPTURE_TREE_INVALID"
    )
    rows = [r for r in tree["tree"] if r["type"] != "tree"]
    prefix = PREFIX + f"{ticker.lower()}/{index}/"
    c.p.need(
        2 <= len(rows) <= MAX_PARTS + 1
        and all(r["type"] == "blob" and r["mode"] == "100644" for r in rows),
        "FOUR_YEAR_GAP_CAPTURE_FILES_INVALID",
    )
    entries = {r["path"]: r for r in rows}
    c.p.need(
        len(entries) == len(rows) and prefix + "manifest.json" in entries,
        "FOUR_YEAR_GAP_CAPTURE_FILES_INVALID",
    )
    files = {r["path"]: archive.blob(r["sha"], r["size"]) for r in rows}
    manifest = c.b.decode(files[prefix + "manifest.json"])
    count = manifest["part_count"]
    c.p.need(
        type(count) is int
        and 1 <= count <= MAX_PARTS
        and set(files)
        == {prefix + "manifest.json", *(prefix + f"part-{i:02}.bin" for i in range(count))},
        "FOUR_YEAR_GAP_CAPTURE_PARTS_INVALID",
    )
    packed = b"".join(files[prefix + f"part-{i:02}.bin"] for i in range(count))
    c.p.need(
        manifest["schema"] == "rotation_four_year_gap_capture_manifest_v1"
        and (source is None or manifest["source_commit"] == source)
        and manifest["plan_sha256"] == PLAN_HASH
        and manifest["policy"] == POLICY
        and manifest["bundle_sha256"] == c.m.sha256(packed),
        "FOUR_YEAR_GAP_CAPTURE_MANIFEST_INVALID",
    )
    held, parent = c.b.read(archive, reference(ticker, index, True), {prefix + "reservation.json"})
    c.p.need(
        parent == c.m.BOOTSTRAP
        and commit["parents"][0]["sha"] == archive.ref(reference(ticker, index, True))
        and held[prefix + "reservation.json"]
        == reservation(ticker, index, manifest["source_commit"]),
        "FOUR_YEAR_GAP_CAPTURE_RESERVATION_INVALID",
    )
    bundle = c.b.decode(bounded_gunzip(packed, MAX_EXPANSION))
    plan = groups(ticker)[index]
    c.p.need(
        bundle["schema"] == "rotation_four_year_gap_source_bundle_v1"
        and len(bundle["rows"]) == manifest["recorded_products"]
        and 0 <= len(bundle["rows"]) <= len(plan),
        "FOUR_YEAR_GAP_CAPTURE_BUNDLE_INVALID",
    )
    c.p.need(
        manifest["unknown_request_product"] is None,
        "FOUR_YEAR_GAP_UNKNOWN_REQUEST_OUTCOME_RETAINED",
    )
    result = {}
    for spec, row in zip(plan[: len(bundle["rows"])], bundle["rows"], strict=True):
        body = base64.b64decode(row["body_base64"], validate=True)
        c.b.secret_free(body, (archive.token,))
        receipt = row["receipt"]
        started, received = (
            datetime.fromisoformat(receipt[k])
            for k in ("request_started_at", "response_received_at")
        )
        c.p.need(
            receipt["product"] == spec
            and receipt["body_sha256"] == c.m.sha256(body)
            and receipt["body_bytes"] == len(body)
            and 0 < len(body) <= c.m.FILE_LIMIT
            and type(receipt["http_status"]) is int
            and started.utcoffset() is not None
            and received.utcoffset() is not None
            and started <= received,
            "FOUR_YEAR_GAP_CAPTURE_RECEIPT_INVALID",
        )
        c.p.need(receipt["http_status"] == 200, "FOUR_YEAR_GAP_RETAINED_HTTP_REJECTED_NO_RETRY")
        decoded = c.decode_market(body)
        c.p.need(decoded.get("adjusted") is not True, "FOUR_YEAR_GAP_ADJUSTED_RESPONSE")
        if spec["kind"] in {"daily", "minute"}:
            c.p.need(decoded.get("ticker") == ticker, "FOUR_YEAR_GAP_RESPONSE_TICKER")
        result[spec["id"]] = {"body": body, "receipt": receipt, "commit": revision}
    c.p.need(len(result) == len(plan), "FOUR_YEAR_GAP_PARTIAL_CAPTURE_RETAINED_NO_RETRY")
    return result


def capture(archive, ticker, source, key_factory, *, verify_only=False, get=fetch, check_plan=True):
    archive.visibility(True)
    c.m.sha(source)
    if check_plan:
        check_inventory(archive)
    result, made = {}, 0
    for index, plan in enumerate(groups(ticker)):
        name, hold = reference(ticker, index), reference(ticker, index, True)
        if archive.ref(name) is None:
            c.p.need(not verify_only, "FOUR_YEAR_GAP_CAPTURE_MISSING")
            c.p.need(archive.ref(hold) is None, "FOUR_YEAR_GAP_UNKNOWN_REQUEST_OUTCOME_RETAINED")
            key = key_factory()
            c.p.need(
                type(key) is str and 0 < len(key) <= 4096 and not any(v.isspace() for v in key),
                "CREDENTIAL_UNAVAILABLE",
            )
            prefix = PREFIX + f"{ticker.lower()}/{index}/"
            budget = c.b.create(
                archive,
                hold,
                {prefix + "reservation.json": reservation(ticker, index, source)},
                c.m.BOOTSTRAP,
            )
            records, unknown = [], None
            for spec in plan:
                start = datetime.now(UTC).isoformat()
                try:
                    status, body = get(spec, key)
                except (TimeoutError, URLError):
                    unknown = spec["id"]
                    break
                made += 1
                c.b.secret_free(body, (key, archive.token))
                c.p.need(type(status) is int and 100 <= status <= 599, "HTTP_STATUS_INVALID")
                records.append(
                    {
                        "receipt": {
                            "product": spec,
                            "http_status": status,
                            "body_sha256": c.m.sha256(body),
                            "body_bytes": len(body),
                            "request_started_at": start,
                            "response_received_at": datetime.now(UTC).isoformat(),
                        },
                        "body_base64": base64.b64encode(body).decode(),
                    }
                )
                invalid = not 0 < len(body) <= c.m.FILE_LIMIT
                if status == 200 and not invalid:
                    try:
                        c.decode_market(body)
                    except (ValueError, c.m.Stopped):
                        invalid = True
                if status != 200 or invalid:
                    break  # Permission/rate failures stop immediately; retain the response, never retry.
            compressed = gzip.compress(
                u.canonical({"schema": "rotation_four_year_gap_source_bundle_v1", "rows": records}),
                mtime=0,
            )
            parts = [
                compressed[i : i + c.m.FILE_LIMIT]
                for i in range(0, len(compressed), c.m.FILE_LIMIT)
            ]
            c.p.need(
                1 <= len(parts) <= MAX_PARTS, "FOUR_YEAR_GAP_ARCHIVE_PACK_EXCEEDS_FIXED_BUDGET"
            )
            files = {prefix + f"part-{i:02}.bin": body for i, body in enumerate(parts)}
            files[prefix + "manifest.json"] = u.canonical(
                {
                    "schema": "rotation_four_year_gap_capture_manifest_v1",
                    "source_commit": source,
                    "plan_sha256": PLAN_HASH,
                    "policy": POLICY,
                    "part_count": len(parts),
                    "recorded_products": len(records),
                    "unknown_request_product": unknown,
                    "bundle_sha256": c.m.sha256(compressed),
                }
            )
            c.b.create(archive, name, files, budget)
        result.update(read_group(archive, ticker, index, source=source))
    return result, made


def guarded(source):
    c.m.sha(source)
    s.need(
        os.environ.get("GITHUB_REPOSITORY") == c.m.CODE
        and os.environ.get("GITHUB_ACTOR") == "dczhhh"
        and os.environ.get("GITHUB_EVENT_NAME") == "push"
        and os.environ.get("GITHUB_REF") == "refs/heads/main"
        and os.environ.get("GITHUB_WORKFLOW_REF") == c.m.CODE + "/" + WORKFLOW + "@refs/heads/main"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and os.environ.get("RUNNER_OS") == "Linux"
        and os.environ.get("ACTIONS_STEP_DEBUG", "").lower() != "true"
        and os.environ.get("ACTIONS_RUNNER_DEBUG", "").lower() != "true"
        and subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() == source,
        "CAPTURE_REVIEWED_RUNNER_CONTEXT",
    )


def main():
    try:
        c.p.need(len(sys.argv) == 4 and sys.argv[1] in {"capture", "verify"}, "ARGUMENTS_INVALID")
        mode, ticker, source = sys.argv[1:]
        guarded(source)
        archive = c.r.CachedArchive(
            c.PacedGitHub(
                c.m.DESTINATION,
                os.environ.get("ARCHIVE_WRITE_TOKEN", ""),
                writable=mode == "capture",
            )
        )
        result, made = capture(
            archive,
            ticker,
            source,
            lambda: os.environ.get("MASSIVE_API_KEY", ""),
            verify_only=mode == "verify",
        )
        print(
            json.dumps(
                {
                    "status": "FOUR_YEAR_GAP_GAP_INPUTS_RETAINED",
                    "ticker": ticker,
                    "products": len(result),
                    "plan_sha256": PLAN_HASH,
                    "new_vendor_requests": made,
                    "new_official_requests": 0,
                    "engine_invoked": False,
                    "PIT_verified": False,
                }
            )
        )
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "FOUR_YEAR_GAP_GAP_CAPTURE_BLOCKED",
                    "reason": c.r.r.code(error),
                    "github_retry_at": getattr(error, "retry_at", None),
                    "engine_invoked": False,
                    **u.safe_failure_location(error),
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
