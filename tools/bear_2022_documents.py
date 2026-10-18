"""Immutable capture of three public filings; no market key and no Engine."""

from __future__ import annotations

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
from tools import bear_2022_capture as a  # noqa: E402
from tools import bear_2022_document_plan as d  # noqa: E402

c = a.c
PREFIX = "research/data/bear_2022_issuer_documents_v1/"
WORKFLOW = ".github/workflows/bear-2022-documents.yml"


def ref(identifier, reservation=False):
    d.product(identifier)
    prefix = "research-reservations/" if reservation else "research-data/"
    return prefix + "bear-2022-issuer-v1-" + identifier


def hold_bytes(identifier, source):
    return d.canonical(
        {
            "product": d.product(identifier),
            "policy": d.POLICY,
            "plan_sha256": d.PLAN_HASH,
            "source_commit": c.m.sha(source),
        }
    )


def fetch(product):
    d.need(product == d.product(product["id"]), "DOCUMENT_SCOPE")
    request = Request(
        product["url"],
        headers={
            "Accept": "text/html",
            "User-Agent": "quant-strategies-research https://github.com/dczhhh/quant-strategies-code",
        },
    )
    try:
        with build_opener(c.m.NoRedirect()).open(request, timeout=45) as response:
            return response.status, response.read(d.DOCUMENT_LIMIT + 1)
    except HTTPError as error:
        with error:
            return error.code, error.read(d.DOCUMENT_LIMIT + 1)


def check_document(body, token):
    d.need(type(body) is bytes and len(body) <= d.DOCUMENT_LIMIT + 1, "DOCUMENT_SIZE")
    # Check across part boundaries too, then keep the original per-blob guard.
    d.need(
        not c.b.CREDENTIAL_PATTERN.search(body) and (not token or token.encode() not in body),
        "DOCUMENT_SECRET_REJECTED",
    )
    for offset in range(0, len(body), c.m.FILE_LIMIT):
        c.b.secret_free(body[offset : offset + c.m.FILE_LIMIT], (token,))


def read(archive, identifier):
    prefix = PREFIX + identifier + "/"
    held, parent = c.b.read(archive, ref(identifier, True), {prefix + "reservation.json"})
    reservation = c.b.decode(held[prefix + "reservation.json"])
    d.need(
        parent == c.m.BOOTSTRAP
        and held[prefix + "reservation.json"]
        == hold_bytes(identifier, reservation["source_commit"]),
        "DOCUMENT_RESERVATION",
    )
    revision = archive.ref(ref(identifier))
    d.need(revision is not None, "DOCUMENT_RAW_MISSING")
    commit = archive.request("GET", "/git/commits/" + c.m.sha(revision))
    tree = archive.request("GET", "/git/trees/" + commit["tree"]["sha"] + "?recursive=1")
    rows = [row for row in tree["tree"] if row["type"] != "tree"]
    d.need(
        tree.get("truncated") is False
        and 1 <= len(rows) <= 17
        and len({row["path"] for row in rows}) == len(rows)
        and all(row["type"] == "blob" and row["mode"] == "100644" for row in rows),
        "DOCUMENT_TREE",
    )
    files = {row["path"]: archive.blob(row["sha"], row["size"]) for row in rows}
    receipt = c.b.decode(files[prefix + "receipt.json"])
    count = receipt["part_count"]
    d.need(
        type(count) is int
        and 0 <= count <= 16
        and set(files)
        == {prefix + "receipt.json", *(prefix + f"part-{i:02}.bin" for i in range(count))}
        and len(commit["parents"]) == 1
        and commit["parents"][0]["sha"] == archive.ref(ref(identifier, True))
        and receipt["reservation_sha256"] == d.digest(held[prefix + "reservation.json"]),
        "DOCUMENT_BINDING",
    )
    body = b"".join(files[prefix + f"part-{i:02}.bin"] for i in range(count))
    start, end = (
        datetime.fromisoformat(receipt[key])
        for key in ("request_started_at", "response_received_at")
    )
    d.need(
        receipt["body_sha256"] == d.digest(body)
        and receipt["body_bytes"] == len(body)
        and start.utcoffset() is not None
        and end.utcoffset() is not None
        and start <= end,
        "DOCUMENT_RECEIPT",
    )
    check_document(body, archive.token)
    d.need(receipt["unknown_outcome"] is False, "DOCUMENT_UNKNOWN_OUTCOME_NO_RETRY")
    status = receipt["http_status"]
    d.need(type(status) is int and 100 <= status <= 599, "DOCUMENT_HTTP_STATUS")
    d.need(status == 200, "DOCUMENT_HTTP_" + str(status) + "_RETAINED_NO_RETRY")
    d.need(receipt["truncated"] is False, "DOCUMENT_OVERSIZE_RETAINED_NO_RETRY")
    return body, receipt


def capture(archive, identifier, source, *, verify_only=False, get=fetch):
    archive.visibility(True)
    product = d.product(identifier)
    held = hold_bytes(identifier, source)
    made = 0
    if archive.ref(ref(identifier)) is None:
        d.need(not verify_only, "DOCUMENT_RAW_MISSING")
        d.need(archive.ref(ref(identifier, True)) is None, "DOCUMENT_UNKNOWN_OUTCOME_NO_RETRY")
        prefix = PREFIX + identifier + "/"
        reservation = c.b.create(
            archive, ref(identifier, True), {prefix + "reservation.json": held}, c.m.BOOTSTRAP
        )
        started, status, body, unknown = datetime.now(UTC).isoformat(), None, b"", False
        try:
            status, body = get(product)
            made = 1
        except (TimeoutError, URLError, c.m.Stopped):
            unknown = True
        check_document(body, archive.token)
        truncated = len(body) > d.DOCUMENT_LIMIT
        body = body[: d.DOCUMENT_LIMIT]
        parts = [body[i : i + c.m.FILE_LIMIT] for i in range(0, len(body), c.m.FILE_LIMIT)]
        receipt = {
            "reservation_sha256": d.digest(held),
            "request_started_at": started,
            "response_received_at": datetime.now(UTC).isoformat(),
            "http_status": status,
            "unknown_outcome": unknown,
            "truncated": truncated,
            "part_count": len(parts),
            "body_bytes": len(body),
            "body_sha256": d.digest(body),
        }
        payload = {prefix + f"part-{i:02}.bin": value for i, value in enumerate(parts)}
        payload[prefix + "receipt.json"] = d.canonical(receipt)
        c.b.create(archive, ref(identifier), payload, reservation)
    body, receipt = read(archive, identifier)
    return body, receipt, made


def guarded(source):
    c.m.sha(source)
    d.need(
        os.environ.get("GITHUB_REPOSITORY") == c.m.CODE
        and os.environ.get("GITHUB_ACTOR") == "dczhhh"
        and os.environ.get("GITHUB_EVENT_NAME") == "push"
        and os.environ.get("GITHUB_REF") == "refs/heads/main"
        and os.environ.get("GITHUB_WORKFLOW_REF") == c.m.CODE + "/" + WORKFLOW + "@refs/heads/main"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and os.environ.get("RUNNER_OS") == "Linux"
        and all(
            os.environ.get(key, "").lower() != "true"
            for key in ("ACTIONS_STEP_DEBUG", "ACTIONS_RUNNER_DEBUG")
        )
        and not os.environ.get("MASSIVE_API_KEY")
        and subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() == source,
        "DOCUMENT_RUNNER_CONTEXT",
    )


def main():
    try:
        d.need(len(sys.argv) == 3 and sys.argv[1] in {"capture", "verify"}, "DOCUMENT_ARGUMENTS")
        mode, source = sys.argv[1:]
        guarded(source)
        archive = c.r.CachedArchive(
            c.PacedGitHub(
                c.m.DESTINATION,
                os.environ.get("ARCHIVE_WRITE_TOKEN", ""),
                writable=mode == "capture",
            )
        )
        for product in d.DOCUMENTS:
            body, _, made = capture(archive, product["id"], source, verify_only=mode == "verify")
            result = d.evidence(product["id"], body)
            path = PREFIX + product["id"] + "/evidence.json"
            payload = {path: d.canonical({"source_commit": source, "evidence": result})}
            audit_ref = ref(product["id"]) + "-evidence"
            if archive.ref(audit_ref) is None:
                d.need(mode == "capture", "DOCUMENT_EVIDENCE_MISSING")
                c.b.create(archive, audit_ref, payload, c.m.BOOTSTRAP)
            saved, parent = c.b.read(archive, audit_ref, set(payload))
            d.need(
                saved == payload and parent == c.m.BOOTSTRAP,
                "DOCUMENT_FRESH_EVIDENCE_BYTES",
            )
            result.update(plan_sha256=d.PLAN_HASH, new_official_requests=made, market_requests=0)
            print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as error:
        message = str(error)
        reason = message if re.fullmatch(r"BEAR2022_[A-Z_0-9]+", message) else "DOCUMENT_UNVERIFIED"
        print(json.dumps({"status": "BLOCKED", "reason": reason, "engine_invoked": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
