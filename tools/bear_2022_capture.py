"""Single-attempt private source qualification for Issue 48, not a backtest."""

from __future__ import annotations

import base64
import gzip
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import bear_2022_sources as s  # noqa: E402
from tools import rotation_stress_capture as g  # noqa: E402

c = g.c
PREFIX = "research/data/bear_2022_source_v1/"
AUDIT_PREFIX = "research/verification/bear_2022_source_audit_v2/"
WORKFLOW = ".github/workflows/bear-2022-source.yml"
BASE = "453dd0dec04daf38b17baeb43eb650a585203b3b"
MAX_PARTS = 4


def ref(ticker, kind):
    s.need(ticker in s.TICKERS and kind in {"raw", "reservation", "audit"}, "ARCHIVE_SCOPE")
    if kind == "audit":
        return "research-data/bear-2022-source-audit-v2-" + ticker.lower()
    prefix = "research-reservations/" if kind == "reservation" else "research-data/"
    return prefix + "bear-2022-source-v1-" + ticker.lower() + "-" + kind


def reservation(ticker, source):
    return s.canonical(
        {
            "source_commit": c.m.sha(source),
            "plan_sha256": s.PLAN_HASH,
            "products": s.products(ticker),
            "policy": s.POLICY,
        }
    )


def fetch(product, key):
    s.need(any(product in s.products(t) for t in s.TICKERS), "REQUEST_OUTSIDE_PLAN")
    headers = {"Accept": "application/json", "User-Agent": "quant-strategies-research/1.0"}
    if product["id"].startswith("massive_"):
        s.need(
            type(key) is str and 0 < len(key) <= 4096 and not any(x.isspace() for x in key),
            "MARKET_CREDENTIAL_UNAVAILABLE",
        )
        headers["Authorization"] = "Bearer " + key
    request = Request(product["url"], headers=headers)
    try:
        with build_opener(c.m.NoRedirect()).open(request, timeout=45) as response:
            return response.status, response.read(c.m.FILE_LIMIT + 1)
    except HTTPError as error:
        with error:
            return error.code, error.read(c.m.FILE_LIMIT + 1)


def read_raw(archive, ticker):
    revision = archive.ref(ref(ticker, "raw"))
    s.need(revision is not None, "RAW_MISSING")
    commit = archive.request("GET", "/git/commits/" + c.m.sha(revision))
    tree = archive.request("GET", "/git/trees/" + commit["tree"]["sha"] + "?recursive=1")
    s.need(tree.get("truncated") is False, "RAW_TREE")
    prefix = PREFIX + ticker.lower() + "/"
    rows = [row for row in tree["tree"] if row["type"] != "tree"]
    s.need(
        2 <= len(rows) <= MAX_PARTS + 1
        and len({r["path"] for r in rows}) == len(rows)
        and all(r["type"] == "blob" and r["mode"] == "100644" for r in rows),
        "RAW_FILES",
    )
    files = {r["path"]: archive.blob(r["sha"], r["size"]) for r in rows}
    manifest = c.b.decode(files[prefix + "manifest.json"])
    count = manifest["part_count"]
    s.need(
        type(count) is int
        and 1 <= count <= MAX_PARTS
        and set(files)
        == {prefix + "manifest.json", *(prefix + f"part-{i:02}.bin" for i in range(count))},
        "RAW_PARTS",
    )
    packed = b"".join(files[prefix + f"part-{i:02}.bin"] for i in range(count))
    s.need(
        manifest["plan_sha256"] == s.PLAN_HASH
        and manifest["policy"] == s.POLICY
        and manifest["bundle_sha256"] == s.digest(packed),
        "RAW_MANIFEST",
    )
    held, parent = c.b.read(archive, ref(ticker, "reservation"), {prefix + "reservation.json"})
    s.need(
        parent == c.m.BOOTSTRAP
        and len(commit["parents"]) == 1
        and commit["parents"][0]["sha"] == archive.ref(ref(ticker, "reservation"))
        and held[prefix + "reservation.json"] == reservation(ticker, manifest["source_commit"]),
        "RAW_RESERVATION",
    )
    bundle = c.b.decode(g.bounded_gunzip(packed, 4 * (c.m.FILE_LIMIT * 2 + 8192)))
    records, plan = bundle["rows"], s.products(ticker)
    s.need(
        len(records) <= len(plan) and len(records) == manifest["recorded_products"], "RAW_RECORDS"
    )
    s.need(manifest["unknown_request_product"] is None, "UNKNOWN_OUTCOME_NO_RETRY")
    result = {}
    for product, row in zip(plan[: len(records)], records, strict=True):
        body = base64.b64decode(row["body_base64"], validate=True)
        receipt = row["receipt"]
        start, end = (
            datetime.fromisoformat(receipt[k])
            for k in ("request_started_at", "response_received_at")
        )
        s.need(
            receipt["product"] == product
            and receipt["body_sha256"] == s.digest(body)
            and receipt["body_bytes"] == len(body)
            and 0 < len(body) <= c.m.FILE_LIMIT
            and start.utcoffset() is not None
            and end.utcoffset() is not None
            and start <= end,
            "RAW_RECEIPT",
        )
        c.b.secret_free(body, (archive.token,))
        status = receipt["http_status"]
        s.need(type(status) is int and 100 <= status <= 599, "RAW_HTTP_STATUS")
        s.need(status == 200, "HTTP_" + str(status) + "_RETAINED_NO_RETRY")
        result[product["id"]] = body
    s.need(len(result) == len(plan), "PARTIAL_CAPTURE_NO_RETRY")
    return result


def capture(archive, ticker, source, key_factory, *, verify_only=False, get=fetch):
    archive.visibility(True)
    c.m.sha(source)
    s.products(ticker)
    prefix = PREFIX + ticker.lower() + "/"
    made = 0
    if archive.ref(ref(ticker, "raw")) is None:
        s.need(not verify_only, "RAW_MISSING")
        s.need(archive.ref(ref(ticker, "reservation")) is None, "UNKNOWN_OUTCOME_NO_RETRY")
        key = key_factory()
        s.need(
            type(key) is str and 0 < len(key) <= 4096 and not any(x.isspace() for x in key),
            "MARKET_CREDENTIAL_UNAVAILABLE",
        )
        hold = c.b.create(
            archive,
            ref(ticker, "reservation"),
            {prefix + "reservation.json": reservation(ticker, source)},
            c.m.BOOTSTRAP,
        )
        records, unknown = [], None
        for product in s.products(ticker):
            start = datetime.now(UTC).isoformat()
            try:
                status, body = get(product, key)
            except (TimeoutError, URLError, c.m.Stopped):
                unknown = product["id"]
                break
            made += 1
            c.b.secret_free(body, (key, archive.token))
            s.need(type(status) is int and 100 <= status <= 599, "HTTP_STATUS")
            records.append(
                {
                    "receipt": {
                        "product": product,
                        "http_status": status,
                        "body_sha256": s.digest(body),
                        "body_bytes": len(body),
                        "request_started_at": start,
                        "response_received_at": datetime.now(UTC).isoformat(),
                    },
                    "body_base64": base64.b64encode(body).decode(),
                }
            )
            if status != 200 or len(body) > c.m.FILE_LIMIT:
                break
        packed = gzip.compress(s.canonical({"rows": records}), mtime=0)
        parts = [packed[i : i + c.m.FILE_LIMIT] for i in range(0, len(packed), c.m.FILE_LIMIT)]
        s.need(1 <= len(parts) <= MAX_PARTS, "RAW_PACK_BUDGET")
        payload = {prefix + f"part-{i:02}.bin": body for i, body in enumerate(parts)}
        payload[prefix + "manifest.json"] = s.canonical(
            {
                "source_commit": source,
                "plan_sha256": s.PLAN_HASH,
                "policy": s.POLICY,
                "part_count": len(parts),
                "recorded_products": len(records),
                "unknown_request_product": unknown,
                "bundle_sha256": s.digest(packed),
            }
        )
        c.b.create(archive, ref(ticker, "raw"), payload, hold)
    return read_raw(archive, ticker), made


def audit(archive, ticker, bodies, source, *, verify_only):
    result = s.qualify(ticker, bodies, c.sessions)
    payload = {
        AUDIT_PREFIX + ticker.lower() + "/source-audit.json": s.canonical(
            {"source_commit": source, "audit": result}
        )
    }
    if archive.ref(ref(ticker, "audit")) is None:
        s.need(not verify_only, "AUDIT_MISSING")
        c.b.create(archive, ref(ticker, "audit"), payload, c.m.BOOTSTRAP)
    saved, parent = c.b.read(archive, ref(ticker, "audit"), set(payload))
    s.need(parent == c.m.BOOTSTRAP and saved == payload, "FRESH_AUDIT_BYTES")
    return result["summary"], s.digest(next(iter(payload.values())))


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
        and all(
            os.environ.get(k, "").lower() != "true"
            for k in ("ACTIONS_STEP_DEBUG", "ACTIONS_RUNNER_DEBUG")
        )
        and subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() == source,
        "RUNNER_CONTEXT",
    )
    changes = subprocess.check_output(
        ["git", "diff", "--name-status", "--no-renames", BASE, source, "--", "src", "tests"],
        text=True,
    ).splitlines()
    s.need(all(row.startswith("A\t") for row in changes), "SEALED_SOURCE_OR_TEST_CHANGED")


def main():
    try:
        s.need(len(sys.argv) == 4 and sys.argv[1] in {"capture", "verify"}, "ARGUMENTS")
        mode, ticker, source = sys.argv[1:]
        guarded(source)
        archive = c.r.CachedArchive(
            c.PacedGitHub(
                c.m.DESTINATION,
                os.environ.get("ARCHIVE_WRITE_TOKEN", ""),
                writable=mode == "capture",
            )
        )
        bodies, made = capture(
            archive,
            ticker,
            source,
            lambda: os.environ.get("MASSIVE_API_KEY", ""),
            verify_only=mode == "verify",
        )
        summary, identity = audit(archive, ticker, bodies, source, verify_only=mode == "verify")
        print(
            json.dumps(
                {
                    "status": "BEAR2022_SOURCE_QUALIFIED_NOT_ECONOMIC_ADMISSION",
                    "ticker": ticker,
                    "mode": mode,
                    "new_source_requests": made,
                    "plan_sha256": s.PLAN_HASH,
                    "audit_sha256": identity,
                    "summary": summary,
                },
                default=str,
                sort_keys=True,
            )
        )
        return 0
    except Exception as error:
        message = str(error)
        reason = (
            message
            if re.fullmatch(r"BEAR2022_[A-Z_0-9]+", message)
            else "UNVERIFIED_SOURCE_FAILURE"
        )
        print(
            json.dumps(
                {
                    "status": "BEAR2022_SOURCE_BLOCKED",
                    "reason": reason,
                    "engine_invoked": False,
                    "performance_admission": False,
                    **g.u.safe_failure_location(error),
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
