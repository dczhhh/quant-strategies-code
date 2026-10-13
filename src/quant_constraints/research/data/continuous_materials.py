"""Two irreversible attempts for ONE frozen AAPL reference request, never bar capture."""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, build_opener

from .archive_contracts import canonical, digest
from .continuous_preparation import PLAN_SHA256, frozen_plan, planned_sessions
from .demand import DemandError, check_secrets, deny
from .demand_storage import private_write
from .massive import NoRedirect
from .private_capture_cli import FLAGS, _root_details
from .private_repo_ingest import FILE_LIMIT, REPOSITORY, GitHub, _blob_sha, _json, _key, _sha

EXECUTION_REF = "refs/heads/research-execution/continuous-materials-v1"
NAMES = ("response.json", "receipt.json", "audit.json")
CLOCKS = dict.fromkeys(("provider_available_at", "published_at", "historically_known_at"), None)
_ROOT_BINDING: dict | None = None


def guarded_root() -> Path:
    global _ROOT_BINDING
    expected = os.environ.get("MASSIVE_CONTINUOUS_REVIEWED_SHA", "")
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
        or os.environ.get("GITHUB_ACTOR") != "dczhhh"
        or os.environ.get("GITHUB_EVENT_NAME") not in {"push", "workflow_dispatch"}
        or os.environ.get("GITHUB_REF") != EXECUTION_REF
        or re.fullmatch(r"[0-9a-f]{40}", expected) is None
        or os.environ.get("GITHUB_SHA") != expected
        or os.environ.get("MASSIVE_CONTINUOUS_ENABLED") != "true"
    ):
        deny("OWNER_CONTINUOUS_JOB_REQUIRED")
    value = os.environ.get("QUANT_CONTINUOUS_ROOT", "")
    if not 0 < len(value) <= 4096 or any(ord(c) < 32 for c in value):
        deny("PRIVATE_ROOT_REQUIRED")
    root = Path(value)
    details = _root_details(root)
    if _ROOT_BINDING is None:
        _ROOT_BINDING = details
    elif details != _ROOT_BINDING:
        deny("PRIVATE_ROOT_CHANGED")
    planned_sessions()
    return root


def reference_facts(content: bytes) -> dict:
    if type(content) is not bytes or not 0 < len(content) <= FILE_LIMIT:
        deny("CONTINUOUS_REFERENCE_BOUNDS")
    check_secrets(content)
    payload = _json(content)
    results = payload.get("results")
    if (
        payload.get("status") != "OK"
        or "next_url" in payload
        or type(results) is not dict
        or type(payload.get("request_id")) is not str
        or re.fullmatch(r"[A-Za-z0-9_-]{1,256}", payload["request_id"]) is None
    ):
        deny("CONTINUOUS_REFERENCE_SCHEMA")
    required = {
        "ticker": "AAPL",
        "cik": "0000320193",
        "type": "CS",
        "market": "stocks",
        "locale": "us",
        "currency_name": "usd",
        "primary_exchange": "XNAS",
        "active": True,
    }
    if any(type(results.get(k)) is not type(v) or results[k] != v for k, v in required.items()):
        deny("CONTINUOUS_REFERENCE_IDENTITY")
    cap = results.get("market_cap")
    if (
        type(cap) not in (int, float)
        or not 10_500_000_000 <= cap <= 10**18
        or not math.isfinite(cap)
    ):
        deny("CONTINUOUS_REFERENCE_CAP_UNCERTAIN")
    # The provider's date parameter can expose a filing by PERIOD, not publication.
    # Therefore neither its financial facts nor its identifiers become historical PIT.
    return {
        "schema_version": "research_continuous_reference_facts_v1",
        "symbol": "AAPL",
        "security_id": "CIK:0000320193",
        "category": "large_cap_retrospective_provider_reference",
        "reference_day": frozen_plan()["materials"]["reference_day"],
        "identity_fields_match_frozen_scope": True,
        "cap_above_conservative_largecap_threshold": True,
        "source_metadata_is_retrospective": True,
        "historical_universe_PIT_verified": False,
        "independent_issuer_original_verified": False,
        "clocks": CLOCKS,
        "may_acquire_aggregates": False,
        "may_replay": False,
        "engine_invoked": False,
        **dict.fromkeys(FLAGS, False),
    }


def _ref(github: GitHub, branch: str) -> str | None:
    result = github.request("GET", "/git/ref/heads/" + branch)
    return None if result is None else _sha(result["object"]["sha"])


def _mapping(github: GitHub, revision: str) -> tuple[dict, dict]:
    scope = frozen_plan()["materials"]
    commit = github.request("GET", "/git/commits/" + _sha(revision))
    if commit is None:
        deny("CONTINUOUS_GIT_BINDING")
    tree = github.request("GET", "/git/trees/" + _sha(commit["tree"]["sha"]) + "?recursive=1")
    if tree is None or tree.get("truncated") is not False:
        deny("CONTINUOUS_GIT_BINDING")
    rows = [r for r in tree["tree"] if r["path"].startswith(scope["path"])]
    if (
        len(rows) != 3
        or {r["path"] for r in rows} != {scope["path"] + n for n in NAMES}
        or any(r["type"] != "blob" or r["mode"] != "100644" for r in rows)
    ):
        deny("CONTINUOUS_EXISTING_CONFLICT")
    return commit, {r["path"]: _sha(r["sha"]) for r in rows}


def _record(body: bytes, revision: str, ordinal: int, stamp: str) -> dict:
    return {
        "schema_version": "research_continuous_reference_receipt_v1",
        "plan_sha256": PLAN_SHA256,
        "request_url": frozen_plan()["materials"]["url"],
        "source_kind": "owner_authorized_http_bytes",
        "source_sha256": digest(body),
        "size_bytes": len(body),
        "source_revision": revision,
        "attempt": ordinal,
        "observed_at": stamp,
        "clocks": CLOCKS,
        **dict.fromkeys(FLAGS, False),
    }


def read_materials(github: GitHub) -> tuple[str, dict[str, bytes]] | None:
    github.private()
    scope = frozen_plan()["materials"]
    revision = _ref(github, scope["branch"])
    if revision is None:
        return None
    commit, mapping = _mapping(github, revision)
    payloads = {}
    for name in NAMES:
        sha = mapping[scope["path"] + name]
        blob = github.request("GET", "/git/blobs/" + sha)
        if blob is None or blob.get("encoding") != "base64":
            deny("CONTINUOUS_GIT_BINDING")
        content = base64.b64decode(blob["content"].replace("\n", ""), validate=True)
        if (
            not 0 < len(content) <= FILE_LIMIT
            or len(content) != blob.get("size")
            or _blob_sha(content) != sha
        ):
            deny("CONTINUOUS_GIT_BINDING")
        payloads[name] = content
    receipt = _json(payloads["receipt.json"])
    ordinal, source = receipt["attempt"], _sha(receipt["source_revision"])
    stamp = datetime.fromisoformat(receipt["observed_at"])
    if (
        type(ordinal) is not int
        or ordinal not in (0, 1)
        or stamp.utcoffset() != UTC.utcoffset(stamp)
        or stamp > datetime.now(UTC)
        or len(commit["parents"]) != 1
        or commit["parents"][0]["sha"] != source
        or source != os.environ.get("GITHUB_SHA")
        or _ref(github, scope["reservation_prefix"] + str(ordinal)) != source
    ):
        deny("CONTINUOUS_RECEIPT_BINDING")
    expected = _record(payloads["response.json"], source, ordinal, receipt["observed_at"])
    if payloads["receipt.json"] != canonical(expected) or payloads["audit.json"] != canonical(
        reference_facts(payloads["response.json"])
    ):
        deny("CONTINUOUS_EXISTING_CONFLICT")
    return revision, payloads


def _publish(github: GitHub, payloads: dict[str, bytes]) -> str:
    scope = frozen_plan()["materials"]
    if set(payloads) != set(NAMES) or any(
        type(v) is not bytes or not 0 < len(v) <= FILE_LIMIT for v in payloads.values()
    ):
        deny("CONTINUOUS_FILE_BOUNDS")
    if _ref(github, scope["branch"]) is not None:
        deny("CONTINUOUS_EXISTING_CONFLICT")
    parent = _sha(os.environ.get("GITHUB_SHA"))
    base = github.request("GET", "/git/commits/" + parent)
    if base is None:
        deny("CONTINUOUS_GIT_BINDING")
    entries = []
    for name, content in payloads.items():
        check_secrets(content, github._token)
        blob = github.write(
            "/git/blobs", {"encoding": "base64", "content": base64.b64encode(content).decode()}
        )
        if blob.get("sha") != _blob_sha(content):
            deny("CONTINUOUS_GIT_BINDING")
        entries.append(
            {"path": scope["path"] + name, "mode": "100644", "type": "blob", "sha": blob["sha"]}
        )
    tree = github.write("/git/trees", {"base_tree": _sha(base["tree"]["sha"]), "tree": entries})
    commit = github.write(
        "/git/commits",
        {
            "tree": _sha(tree["sha"]),
            "parents": [parent],
            "message": "archive: bounded untrusted AAPL reference [skip ci]",
        },
    )
    revision = _sha(commit["sha"])
    ref = github.write("/git/refs", {"ref": "refs/heads/" + scope["branch"], "sha": revision})
    if ref.get("object", {}).get("sha") != revision or read_materials(github) != (
        revision,
        payloads,
    ):
        deny("CONTINUOUS_GIT_BINDING")
    return revision


def capture(root: Path, github: GitHub) -> dict:
    if guarded_root() != root:
        deny("PRIVATE_ROOT_CHANGED")
    cached = read_materials(github)
    if cached is not None:
        return {"market_requests": 0, "cache_reused": True, "source_commit": cached[0]}
    if (root / "native/started.json").exists():
        deny("CONTINUOUS_PARTIAL_REVIEW_REQUIRED")
    scope, source = frozen_plan()["materials"], _sha(os.environ.get("GITHUB_SHA"))
    key = _key("MASSIVE_API_KEY")
    ordinal = next(
        (i for i in range(2) if _ref(github, scope["reservation_prefix"] + str(i)) is None), None
    )
    if ordinal is None:
        deny("CONTINUOUS_REFERENCE_BUDGET_EXHAUSTED")
    marker = github.write(
        "/git/refs",
        {"ref": "refs/heads/" + scope["reservation_prefix"] + str(ordinal), "sha": source},
    )
    if marker.get("object", {}).get("sha") != source or guarded_root() != root:
        deny("CONTINUOUS_GIT_BINDING")
    private_write(root, "native/started.json", canonical({"attempt": ordinal}))
    github.private()
    if guarded_root() != root:
        deny("PRIVATE_ROOT_CHANGED")
    request = Request(
        scope["url"],
        headers={"Accept": "application/json", "Authorization": "Bearer " + key},
        method="GET",
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=20) as response:
            if response.status != 200:
                deny("CONTINUOUS_MARKET_REQUEST_STOPPED")
            content = response.read(FILE_LIMIT + 1)
    except HTTPError as error:
        error.close()
        deny("CONTINUOUS_MARKET_REQUEST_STOPPED")
    check_secrets(content, key)
    check_secrets(content, github._token)
    facts = reference_facts(content)
    receipt = _record(content, source, ordinal, datetime.now(UTC).isoformat())
    payloads = {
        "response.json": content,
        "receipt.json": canonical(receipt),
        "audit.json": canonical(facts),
    }
    for name, body in payloads.items():
        private_write(root, "native/" + name, body)
    if guarded_root() != root:
        deny("PRIVATE_ROOT_CHANGED")
    revision = _publish(github, payloads)
    return {"market_requests": 1, "cache_reused": False, "source_commit": revision}


class _SafeParser(argparse.ArgumentParser):
    def error(self, message):
        deny("CONTINUOUS_COMMAND_INVALID")


def main(argv: list[str] | None = None) -> int:
    try:
        parser = _SafeParser()
        parser.add_argument("mode", choices=("preflight", "capture", "verify"))
        args = parser.parse_args(argv)
        root, extra = guarded_root(), {}
        github = GitHub()
        github.private()
        if args.mode == "capture":
            extra = capture(root, github)
        elif args.mode == "verify":
            result = read_materials(github)
            if result is None:
                deny("CONTINUOUS_MATERIALS_MISSING")
            extra = {"source_commit": result[0], "market_requests": 0, "materials_verified": True}
        code, status, failed = "CONTINUOUS_OPERATION_COMPLETE", "untrusted_materials_only", False
    except DemandError as error:
        code, status, failed, extra = error.code, "fail_closed", True, {}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        code, status, failed, extra = "CONTINUOUS_OPERATION_STOPPED", "fail_closed", True, {}
    print(
        json.dumps(
            {
                "schema_version": "research_continuous_materials_audit_v1",
                "code": code,
                "status": status,
                **extra,
                **dict.fromkeys(FLAGS, False),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
