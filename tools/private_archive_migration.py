"""Copy one reviewed private inventory in GitHub runners; never request vendor data."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

SOURCE = "dczhhh/quant-strategies"
DESTINATION = "dczhhh/quant-strategies-data"
CODE = "dczhhh/quant-strategies-code"
BOOTSTRAP = "99ec4f6a0df613f86180f6496c34845fd45b69ec"
INVENTORY_BLOB = "3cf65159e430bffe92681a77b793f9f4a097f7d1"
MIGRATION_REF = "research-migrations/source-archive-v1"
INVENTORY_PATH = "migration/source-inventory-v1.json"
MAP_PATH = "migration/readback-map-v1.json"
FLAGS = ("raw_verified", "PIT_verified", "archive_ready", "trusted_entry", "may_build_engine")
FILE_LIMIT = 524_288
TOTAL_LIMIT = 2_097_152
RESPONSE_LIMIT = 1_048_576
CALL_LIMIT = 800


class Stopped(Exception):
    """No provider body, credential, URL or private error text in the public log."""

    def __init__(self, code: str = "CHECK_FAILED"):
        super().__init__()
        self.code = code


def require(condition: bool) -> None:
    if not condition:
        raise Stopped()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def git_blob(content: bytes) -> str:
    return hashlib.sha1(
        b"blob " + str(len(content)).encode() + b"\0" + content, usedforsecurity=False
    ).hexdigest()


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha(value: object) -> str:
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{40}", value) is not None)
    return str(value)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Stopped()


class GitHub:
    def __init__(self, repository: str, token: str = "", writable: bool = False):
        require(repository in {SOURCE, DESTINATION, CODE})
        require(not writable or repository == DESTINATION)
        require(
            repository == CODE or (0 < len(token) <= 4096 and not any(c.isspace() for c in token))
        )
        self.repository = repository
        self.token = token
        self.writable = writable
        self.calls = 0

    def request(self, method: str, suffix: str, payload: dict | None = None) -> dict | None:
        self.calls += 1
        require(self.calls <= CALL_LIMIT)
        allowed_get = suffix == "" or re.fullmatch(
            r"/git/(?:blobs|commits)/[0-9a-f]{40}|/git/trees/[0-9a-f]{40}\?recursive=1"
            r"|/git/ref/heads/[A-Za-z0-9_./-]+",
            suffix,
        )
        require(
            (method == "GET" and bool(allowed_get) and payload is None)
            or (
                method == "POST"
                and self.writable
                and suffix in {"/git/blobs", "/git/trees", "/git/commits", "/git/refs"}
                and type(payload) is dict
            )
        )
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        body = None if payload is None else canonical(payload)
        require(body is None or len(body) <= RESPONSE_LIMIT)
        request = Request(
            "https://api.github.com/repos/" + self.repository + suffix,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with build_opener(NoRedirect()).open(request, timeout=20) as response:
                require(response.status in {200, 201})
                content = response.read(RESPONSE_LIMIT + 1)
        except HTTPError as error:
            with error:
                if method == "GET" and error.code == 404 and suffix.startswith("/git/ref/heads/"):
                    return None
                code = (
                    f"GITHUB_HTTP_{error.code}"
                    if error.code in {401, 403, 404, 429}
                    else "GITHUB_HTTP_FAILED"
                )
            raise Stopped(code) from None
        require(len(content) <= RESPONSE_LIMIT)
        result = json.loads(content)
        require(type(result) is dict)
        return result

    def visibility(self, private: bool) -> None:
        info = self.request("GET", "")
        require(
            info is not None
            and info.get("full_name") == self.repository
            and info.get("private") is private
            and info.get("visibility") == ("private" if private else "public")
        )

    def write(self, suffix: str, payload: dict) -> dict:
        require(self.writable and self.repository == DESTINATION)
        self.visibility(True)  # Recheck before EVERY write, including Git blobs.
        result = self.request("POST", suffix, payload)
        require(result is not None)
        return result

    def blob(self, identity: str, expected_size: int | None = None) -> bytes:
        blob = self.request("GET", "/git/blobs/" + sha(identity))
        require(blob is not None and blob.get("encoding") == "base64")
        require(type(blob.get("content")) is str and type(blob.get("size")) is int)
        require(0 < blob["size"] <= FILE_LIMIT)
        data = base64.b64decode(blob["content"].replace("\n", ""), validate=True)
        require(len(data) == blob["size"] and git_blob(data) == identity)
        require(expected_size is None or len(data) == expected_size)
        return data

    def ref(self, name: str) -> str | None:
        require(
            re.fullmatch(
                r"research-(?:data|sources|reservations|migrations)/[A-Za-z0-9_./-]+", name
            )
            is not None
        )
        result = self.request("GET", "/git/ref/heads/" + name)
        return None if result is None else sha(result["object"]["sha"])


def source_files(inventory: dict) -> list[dict]:
    require(inventory.get("schema_version") == "private_archive_migration_inventory_v1")
    require(
        inventory.get("source_repository") == SOURCE
        and inventory.get("destination_repository") == DESTINATION
        and inventory.get("status") == "INVENTORY_ONLY_NOT_MIGRATED"
    )
    require(
        type(inventory.get("archive_files_copied")) is int
        and inventory["archive_files_copied"] == 0
        and inventory.get("allow_public_raw_data") is False
    )
    flags = inventory.get("execution_admission", {})
    require(
        all(flags.get(key) is False for key in FLAGS)
        and flags.get("engine_invoked") is False
        and flags.get("strategy_performance_computed") is False
    )
    require(type(inventory.get("archives")) is list and len(inventory["archives"]) == 22)
    require(type(inventory.get("reservations")) is list and len(inventory["reservations"]) == 26)
    rows = []
    references = []
    for archive in inventory["archives"]:
        reference = archive["source_ref"]
        require(
            type(reference) is str
            and re.fullmatch(r"refs/heads/research-(?:data|sources)/[A-Za-z0-9_./-]+", reference)
            is not None
        )
        references.append(reference)
        sha(archive["source_commit"])
        sha(archive["source_tree"])
        require(type(archive["source_parents"]) is list and len(archive["source_parents"]) == 1)
        sha(archive["source_parents"][0])
        require(
            archive.get("status") == "NOT_MIGRATED"
            and type(archive["files"]) is list
            and 0 < len(archive["files"]) <= 9
        )
        for row in archive["files"]:
            path = row["path"]
            require(
                type(path) is str
                and len(path) <= 240
                and re.fullmatch(r"research/(?:data|sources|reports)/[A-Za-z0-9_./-]+", path)
                is not None
                and all(part not in {"", ".", ".."} for part in path.split("/"))
            )
            require(
                row["mode"] == "100644"
                and type(row["size_bytes"]) is int
                and 0 < row["size_bytes"] <= FILE_LIMIT
            )
            sha(row["git_blob_sha"])
            rows.append(row)
    for row in inventory["reservations"]:
        reference = row["source_ref"]
        require(
            type(reference) is str
            and re.fullmatch(r"refs/heads/research-reservations/[A-Za-z0-9_./-]+", reference)
            is not None
        )
        references.append(reference)
        sha(row["source_commit"])
        require(row.get("status") == "SOURCE_MARKER_PRESERVED_NOT_RESET")
    require(len(set(references)) == len(references))
    require(len(rows) == 91 and len({row["path"] for row in rows}) == 91)
    require(sum(row["size_bytes"] for row in rows) <= TOTAL_LIMIT)
    return sorted(rows, key=lambda row: row["path"])


def inventory_bytes(destination: GitHub) -> bytes:
    destination.visibility(True)
    commit = destination.request("GET", "/git/commits/" + BOOTSTRAP)
    require(commit is not None)
    tree = destination.request("GET", "/git/trees/" + sha(commit["tree"]["sha"]) + "?recursive=1")
    require(tree is not None and tree.get("truncated") is False)
    matches = [row for row in tree["tree"] if row["path"] == INVENTORY_PATH]
    require(
        len(matches) == 1
        and matches[0]["type"] == "blob"
        and matches[0]["mode"] == "100644"
        and matches[0]["sha"] == INVENTORY_BLOB
    )
    content = destination.blob(INVENTORY_BLOB)
    require(len(content) <= 65_536)
    source_files(json.loads(content))
    return content


def source_unchanged(source: GitHub, inventory: dict) -> None:
    source.visibility(True)
    for row in inventory["archives"] + inventory["reservations"]:
        require(source.ref(row["source_ref"].removeprefix("refs/heads/")) == row["source_commit"])


def validate_metadata(inventory: dict, payloads: dict[str, bytes]) -> None:
    by_commit = {row["source_commit"]: row for row in inventory["archives"]}
    for path, content in payloads.items():
        if not path.endswith(("/manifest.json", "/receipt.json")):
            continue
        record = json.loads(content)
        require(type(record) is dict and all(record.get(key) is False for key in FLAGS))
        prefix = path.rsplit("/", 1)[0] + "/"
        if path.endswith("/receipt.json"):
            response = payloads[prefix + "response.json"]
            require(
                record.get("source_sha256") == sha256(response)
                and type(record.get("size_bytes")) is int
                and record["size_bytes"] == len(response)
            )
            continue
        require(type(record.get("files")) is dict and 0 < len(record["files"]) <= 8)
        for name, info in record["files"].items():
            require(
                type(name) is str
                and re.fullmatch(r"[A-Za-z0-9_-]+\.[a-z]+", name) is not None
                and type(info) is dict
                and set(info) == {"sha256", "size_bytes"}
            )
            body = payloads[prefix + name]
            require(
                type(info["size_bytes"]) is int
                and info["size_bytes"] == len(body)
                and info["sha256"] == sha256(body)
            )
        if "source_sha256" in record:
            require(set(record["source_sha256"]) == {"raw", "comparison"})
            for product, expected in record["source_sha256"].items():
                require(expected == sha256(payloads[prefix + product + "_response.json"]))
        if "source_bindings" in record:
            require(type(record["source_bindings"]) is dict and record["source_bindings"])
            for binding in record["source_bindings"].values():
                original = by_commit[sha(binding["source_commit"])]
                named = {row["path"].rsplit("/", 1)[1]: row["path"] for row in original["files"]}
                require(
                    binding["receipt_sha256"] == sha256(payloads[named["receipt.json"]])
                    and binding["response_sha256"] == sha256(payloads[named["response.json"]])
                )


def mapping(content: bytes, payloads: dict[str, bytes]) -> bytes:
    inventory = json.loads(content)
    rows = source_files(inventory)
    require(set(payloads) == {row["path"] for row in rows})
    validate_metadata(inventory, payloads)
    return canonical(
        {
            "schema_version": "private_archive_readback_map_v1",
            "source_repository": SOURCE,
            "source_inventory_git_blob_sha": INVENTORY_BLOB,
            "source_inventory_sha256": sha256(content),
            "destination_repository": DESTINATION,
            "destination_parent": BOOTSTRAP,
            "source_payloads_unchanged": True,
            "source_reservations_preserved_not_reset": True,
            "vendor_requests": 0,
            "engine_invoked": False,
            "strategy_performance_computed": False,
            **dict.fromkeys(FLAGS, False),
            "files": [{**row, "sha256": sha256(payloads[row["path"]])} for row in rows],
        }
    )


def verify(destination: GitHub, content: bytes) -> None:
    require(git_blob(content) == INVENTORY_BLOB)
    destination.visibility(True)
    revision = destination.ref(MIGRATION_REF)
    require(revision is not None)
    commit = destination.request("GET", "/git/commits/" + sha(revision))
    require(commit is not None and [parent["sha"] for parent in commit["parents"]] == [BOOTSTRAP])
    tree = destination.request("GET", "/git/trees/" + sha(commit["tree"]["sha"]) + "?recursive=1")
    require(tree is not None and tree.get("truncated") is False)
    actual = {row["path"]: row for row in tree["tree"] if row["type"] != "tree"}
    expected = source_files(json.loads(content))
    require(
        len(actual) == len(expected) + 2
        and set(actual) == {row["path"] for row in expected} | {MAP_PATH, INVENTORY_PATH}
    )
    require(all(row["type"] == "blob" and row["mode"] == "100644" for row in actual.values()))
    require(
        actual[INVENTORY_PATH]["sha"] == INVENTORY_BLOB
        and destination.blob(INVENTORY_BLOB) == content
    )
    payloads = {}
    for row in expected:
        require(
            actual[row["path"]]["sha"] == row["git_blob_sha"]
            and actual[row["path"]]["size"] == row["size_bytes"]
        )
        payloads[row["path"]] = destination.blob(row["git_blob_sha"], row["size_bytes"])
    require(destination.blob(actual[MAP_PATH]["sha"]) == mapping(content, payloads))
    destination.visibility(True)


def migrate(source: GitHub, destination: GitHub, content: bytes) -> None:
    require(git_blob(content) == INVENTORY_BLOB)
    destination.visibility(True)
    inventory = json.loads(content)
    source_unchanged(source, inventory)
    if destination.ref(MIGRATION_REF) is not None:
        verify(destination, content)
        source_unchanged(source, inventory)
        return  # Fresh runner replay: no writes and no source payload download.
    payloads = {}
    for archive in inventory["archives"]:
        commit = source.request("GET", "/git/commits/" + archive["source_commit"])
        require(
            commit is not None
            and commit["tree"]["sha"] == archive["source_tree"]
            and [parent["sha"] for parent in commit["parents"]] == archive["source_parents"]
        )
        tree = source.request("GET", "/git/trees/" + archive["source_tree"] + "?recursive=1")
        require(tree is not None and tree.get("truncated") is False)
        indexed = {row["path"]: row for row in tree["tree"]}
        for row in archive["files"]:
            actual = indexed.get(row["path"], {})
            require(
                actual.get("sha") == row["git_blob_sha"]
                and actual.get("mode") == "100644"
                and actual.get("type") == "blob"
                and actual.get("size") == row["size_bytes"]
            )
            body = source.blob(row["git_blob_sha"], row["size_bytes"])
            require(source.token.encode() not in body and destination.token.encode() not in body)
            payloads[row["path"]] = body
    source_unchanged(source, inventory)
    record = mapping(content, payloads)
    entries = []
    for path, body in sorted({**payloads, INVENTORY_PATH: content, MAP_PATH: record}.items()):
        require(len(body) <= FILE_LIMIT)
        identity = destination.write(
            "/git/blobs", {"encoding": "base64", "content": base64.b64encode(body).decode()}
        )
        require(identity.get("sha") == git_blob(body))
        entries.append({"path": path, "type": "blob", "mode": "100644", "sha": identity["sha"]})
    tree = destination.write("/git/trees", {"tree": entries})
    commit = destination.write(
        "/git/commits",
        {
            "message": "archive: copy frozen private source payloads without changing provenance",
            "tree": sha(tree["sha"]),
            "parents": [BOOTSTRAP],
        },
    )
    source_unchanged(source, inventory)
    destination.write(
        "/git/refs", {"ref": "refs/heads/" + MIGRATION_REF, "sha": sha(commit["sha"])}
    )
    verify(destination, content)
    source_unchanged(source, inventory)


def guarded() -> None:
    expected = os.environ.get("ARCHIVE_MIGRATION_REVIEWED_SHA", "")
    require(
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and os.environ.get("GITHUB_REPOSITORY") == CODE
        and os.environ.get("GITHUB_ACTOR") == "dczhhh"
        and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        and os.environ.get("GITHUB_REF") == "refs/heads/main"
        and os.environ.get("GITHUB_SHA") == sha(expected)
        and os.environ.get("ARCHIVE_MIGRATION_ENABLED") == "true"
    )
    GitHub(CODE).visibility(False)
    require(
        os.environ.get("ACTIONS_STEP_DEBUG", "").lower() != "true"
        and os.environ.get("ACTIONS_RUNNER_DEBUG", "").lower() != "true"
    )


def main() -> int:
    phase = "context"
    try:
        require(sys.argv[1:] in [["migrate"], ["verify"]])
        guarded()
        phase = "destination_inventory"
        destination = GitHub(DESTINATION, os.environ.get("ARCHIVE_WRITE_TOKEN", ""), writable=True)
        content = inventory_bytes(destination)
        if sys.argv[1] == "migrate":
            phase = "private_copy"
            source = GitHub(SOURCE, os.environ.get("LEGACY_SOURCE_READ_TOKEN", ""))
            migrate(source, destination, content)
        else:
            phase = "private_readback"
            verify(destination, content)
    except Exception as error:
        allowed_codes = {
            "CHECK_FAILED",
            "GITHUB_HTTP_401",
            "GITHUB_HTTP_403",
            "GITHUB_HTTP_404",
            "GITHUB_HTTP_429",
            "GITHUB_HTTP_FAILED",
        }
        code = (
            error.code
            if isinstance(error, Stopped) and error.code in allowed_codes
            else "CHECK_FAILED"
        )
        print(
            json.dumps(
                {"status": "MIGRATION_STOPPED", "vendor_requests": 0, "phase": phase, "code": code},
                separators=(",", ":"),
            )
        )
        return 1
    print('{"status":"MIGRATION_VERIFIED","vendor_requests":0}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
