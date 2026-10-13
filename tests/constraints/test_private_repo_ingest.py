"""Invented GitHub/provider transport; no real secrets, archive or remote writes."""

import base64
import io
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from quant_constraints.research.data import demand_capture
from quant_constraints.research.data import private_repo_ingest as ingest
from quant_constraints.research.data.archive_contracts import canonical, digest
from quant_constraints.research.data.demand import DemandError

AS_OF = datetime(2026, 10, 12, 11, tzinfo=UTC)


def rows():
    return [
        dict(zip(ingest.COLUMNS, [1791552600000 + 60000 * i, 100.0, 101.0, 99.0, 100.5, 50.0]))
        for i in range(390)
    ]


def provider_page(adjusted):
    return {
        "ticker": "AAPL",
        "adjusted": adjusted,
        "status": "OK",
        "queryCount": 390,
        "resultsCount": 390,
        "request_id": "invented_request",
        "results": [dict(zip(ingest.VENDOR_COLUMNS, list(r.values()))) for r in rows()],
    }


def bundle(root):
    ingest.create_bundle(
        root,
        {"raw": rows(), "comparison": rows()},
        {"raw": canonical(provider_page(False)), "comparison": canonical(provider_page(True))},
    )


class Transport:
    def __init__(self):
        self.calls = []
        self.blobs = {}
        self.tree = []
        self.ref = None
        self.reservations = {}
        self.problem = None
        self.response_reads = []

    def open(self, request, timeout):
        self.calls.append((request.get_method(), request.full_url, request.data))
        assert timeout == 20
        if request.full_url.startswith("https://api.massive.com"):
            product = "adjusted=true" in request.full_url
            body = {
                "ticker": "AAPL",
                "adjusted": product,
                "status": "OK",
                "queryCount": 390,
                "resultsCount": 390,
                "count": 390,
                "request_id": "invented_request",
                "results": [dict(zip(ingest.VENDOR_COLUMNS, list(r.values()))) for r in rows()],
            }
            if self.problem == "pagination":
                body["next_url"] = request.full_url + "&cursor=invented"
            if self.problem == "provider_schema":
                body["ticker"] = "BAD"
            encoded = canonical(body)
            if self.problem == "provider_key":
                encoded = encoded.replace(b"invented_request", b"fixture_provider_key")
            if self.problem == "oversize":
                encoded = b" " * (ingest.FILE_LIMIT + 1)
            if self.problem == "http":
                raise HTTPError(request.full_url, 403, "DO_NOT_PRINT", {}, io.BytesIO(b"PRIVATE"))
            if self.problem == "transport":
                raise URLError("DO_NOT_PRINT")
            status = 429 if self.problem == "status" else 200
        else:
            prefix = "https://api.github.com/repos/" + ingest.REPOSITORY
            assert request.full_url.startswith(prefix)
            suffix = request.full_url.removeprefix(prefix)
            payload = None if request.data is None else json.loads(request.data)
            status = 200
            if suffix == "":
                body = {
                    "full_name": ingest.REPOSITORY,
                    "private": self.problem != "public",
                    "visibility": "private",
                }
                if self.problem == "flip_public" and self.blobs:
                    body["private"] = False
            elif suffix.startswith("/git/ref/"):
                branch = suffix.removeprefix("/git/ref/heads/")
                value = self.ref if branch == ingest.DATA_BRANCH else self.reservations.get(branch)
                if value is None:
                    raise HTTPError(request.full_url, 404, "missing", {}, io.BytesIO())
                body = {"object": {"sha": value}}
            elif suffix.startswith("/git/commits/"):
                body = {"tree": {"sha": "c" * 40}}
            elif suffix.startswith("/git/trees/"):
                body = {"truncated": self.problem == "truncated", "tree": self.tree}
            elif suffix.startswith("/git/blobs/"):
                data = self.blobs[suffix.rsplit("/", 1)[1]]
                body = {
                    "encoding": "base64",
                    "size": len(data),
                    "content": base64.b64encode(data).decode(),
                }
                if self.problem == "blob_encoding":
                    body["encoding"] = "utf8"
                if self.problem == "blob_size":
                    body["size"] += 1
            elif suffix == "/git/blobs":
                data = base64.b64decode(payload["content"])
                sha = ingest._blob_sha(data)
                self.blobs[sha] = data
                body = {"sha": "d" * 40 if self.problem == "wrong_blob" else sha}
                status = 201
            elif suffix == "/git/trees":
                self.tree = payload["tree"]
                body = {"sha": "c" * 40}
                status = 201
            elif suffix == "/git/commits":
                body = {"sha": "d" * 40}
                status = 201
            elif suffix == "/git/refs":
                branch = payload["ref"].removeprefix("refs/heads/")
                if branch == ingest.DATA_BRANCH:
                    self.ref = payload["sha"]
                else:
                    assert branch.startswith("research-reservations/aapl-v1-pair-")
                    self.reservations[branch] = payload["sha"]
                body = {
                    "object": {"sha": "f" * 40 if self.problem == "wrong_ref" else payload["sha"]}
                }
                status = 201
            else:
                raise AssertionError(suffix)
            encoded = canonical(body)
        outer = self

        class Response:
            def __init__(self):
                self.status = status

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size):
                outer.response_reads.append(size)
                return encoded[:size]

        return Response()


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    root.mkdir(mode=0o700)
    for name, value in {
        "GITHUB_ACTIONS": "true",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "GITHUB_REPOSITORY": ingest.REPOSITORY,
        "GITHUB_ACTOR": "dczhhh",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": ingest.EXECUTION_REF,
        "GITHUB_SHA": "a" * 40,
        "MASSIVE_ARCHIVE_REVIEWED_SHA": "a" * 40,
        "MASSIVE_ARCHIVE_ENABLED": "true",
        "QUANT_CAPTURE_ROOT": str(root),
        "GITHUB_TOKEN": "fixture_github_key",
        "MASSIVE_API_KEY": "fixture_provider_key",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(ingest, "_ROOT_BINDING", None)
    monkeypatch.setattr(ingest, "now", lambda: AS_OF)
    monkeypatch.setattr(
        ingest, "_root_details", lambda path: {"path": str(path), "inode": path.stat().st_ino}
    )
    monkeypatch.setattr(ingest.time, "sleep", lambda _interval: None)
    transport = Transport()
    monkeypatch.setattr(ingest, "build_opener", lambda *_args: transport)
    return root, transport


def test_complete_native_capture_publish_and_remote_cache_without_repeated_market_requests(
    runtime, tmp_path, monkeypatch, capsys
):
    root, transport = runtime
    assert ingest.main(["preflight"]) == 0
    assert ingest.main(["capture"]) == 0
    assert ingest.main(["publish"]) == 0
    assert ingest.main(["publish"]) == 0
    assert ingest.main(["capture"]) == 0
    output = capsys.readouterr().out
    assert str(root) not in output and "fixture_" not in output
    assert sum(url.startswith("https://api.massive.com") for _, url, _ in transport.calls) == 2
    assert all(size in {ingest.FILE_LIMIT + 1, 1048577} for size in transport.response_reads)
    assert len(transport.blobs) >= 4 and len(transport.tree) == 8
    assert all(item["path"].startswith(ingest.DATA_PATH) for item in transport.tree)
    reports = [json.loads(line) for line in output.splitlines()]
    assert all(all(r[f] is False for f in ingest.FLAGS) for r in reports)
    assert demand_capture._ACCEPTED_EXECUTION_REVIEWS == ()
    second = tmp_path / "second"
    second.mkdir(mode=0o700)
    monkeypatch.setenv("QUANT_CAPTURE_ROOT", str(second))
    monkeypatch.setattr(ingest, "_ROOT_BINDING", None)
    monkeypatch.delenv("MASSIVE_API_KEY")
    assert ingest.main(["capture"]) == 0
    assert ingest.load_bundle(second) == ingest.load_bundle(root)
    assert sum(url.startswith("https://api.massive.com") for _, url, _ in transport.calls) == 2


@pytest.mark.parametrize("product", ["raw", "comparison"])
def test_optional_provider_count_alias_is_validated_without_rewriting_archived_bytes(
    runtime, product
):
    root, _transport = runtime
    page = provider_page(product == "comparison")
    page["count"] = 390
    original = json.dumps(page, indent=2).encode()
    assert ingest.source_rows(original, product) == rows()
    sources = {
        "raw": original if product == "raw" else canonical(provider_page(False)),
        "comparison": original if product == "comparison" else canonical(provider_page(True)),
    }
    ingest.create_bundle(root, {"raw": rows(), "comparison": rows()}, sources)
    files = ingest.load_bundle(root)
    assert files[product + "_response.json"] == original
    assert json.loads(files["manifest.json"])["source_sha256"][product] == digest(original)


@pytest.mark.parametrize("count", [True, 390.0, "390", None, -1, 0, 391])
def test_provider_count_alias_conflicts_stop_before_archive(runtime, count):
    root, _transport = runtime
    page = provider_page(False)
    page["count"] = count
    with pytest.raises(DemandError, match="PROVIDER_COUNTS_INVALID"):
        ingest.source_rows(canonical(page), "raw")
    assert not (root / "upload").exists()


def test_count_alias_does_not_allow_unknown_metadata_or_duplicate_json_fields(runtime):
    page = provider_page(False)
    page["count"] = 390
    page["unapproved_metadata"] = "invented"
    with pytest.raises(DemandError, match="PROVIDER_SCHEMA_CONFLICT"):
        ingest.source_rows(canonical(page), "raw")
    del page["unapproved_metadata"]
    duplicated = canonical(page).replace(b'"count":390', b'"count":390,"count":390')
    with pytest.raises(DemandError):
        ingest.source_rows(duplicated, "raw")


@pytest.mark.parametrize(
    "name,value",
    [
        ("GITHUB_ACTIONS", "false"),
        ("RUNNER_ENVIRONMENT", "self-hosted"),
        ("GITHUB_REPOSITORY", "other/private"),
        ("GITHUB_ACTOR", "other"),
        ("GITHUB_EVENT_NAME", "pull_request"),
        ("GITHUB_REF", "refs/heads/main"),
        ("GITHUB_SHA", "b" * 40),
        ("MASSIVE_ARCHIVE_REVIEWED_SHA", ""),
        ("MASSIVE_ARCHIVE_ENABLED", "false"),
    ],
)
def test_unapproved_job_stops_before_root_credential_or_transport(
    runtime, monkeypatch, capsys, name, value
):
    _, transport = runtime
    monkeypatch.setenv(name, value)
    monkeypatch.setattr(ingest, "_root_details", lambda _path: pytest.fail("root inspected"))
    monkeypatch.setattr(ingest, "_key", lambda _name: pytest.fail("credential inspected"))
    assert ingest.main(["capture"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "OWNER_GITHUB_JOB_REQUIRED"
    assert transport.calls == []


@pytest.mark.parametrize("key", ["", "bad\nkey", "x" * 4097], ids=["empty", "newline", "oversize"])
def test_invalid_keys_do_not_enter_http_headers(runtime, monkeypatch, capsys, key):
    _, transport = runtime
    monkeypatch.setenv("GITHUB_TOKEN", key)
    assert ingest.main(["capture"]) == 2
    assert transport.calls == []
    assert json.loads(capsys.readouterr().out)["code"] == "CREDENTIAL_UNAVAILABLE"


@pytest.mark.parametrize(
    "problem",
    ["http", "transport", "status", "oversize", "provider_key", "provider_schema", "pagination"],
)
def test_provider_failure_stops_no_retry_no_export_or_upload(runtime, capsys, problem):
    root, transport = runtime
    transport.problem = problem
    assert ingest.main(["capture"]) == 2
    output = capsys.readouterr().out
    assert "DO_NOT_PRINT" not in output and "fixture_provider_key" not in output
    assert not (root / "upload/manifest.json").exists()
    assert ingest.main(["capture"]) == 2
    assert sum(url.startswith("https://api.massive.com") for _, url, _ in transport.calls) == 1
    assert not transport.blobs


@pytest.mark.parametrize("problem", ["public", "flip_public", "wrong_blob", "wrong_ref"])
def test_private_visibility_and_git_hash_checks_stop_publication(runtime, capsys, problem):
    root, transport = runtime
    bundle(root)
    transport.problem = problem
    assert ingest.main(["publish"]) == 2
    assert "fixture_" not in capsys.readouterr().out
    if problem in {"public", "flip_public", "wrong_blob"}:
        assert transport.ref is None


@pytest.mark.parametrize(
    "problem",
    [
        "truncated",
        "extra",
        "mode",
        "missing",
        "duplicate",
        "blob_encoding",
        "blob_size",
        "conflict",
    ],
)
def test_bad_remote_cache_stops_without_provider_key_or_market_download(
    runtime, tmp_path, monkeypatch, capsys, problem
):
    root, transport = runtime
    bundle(root)
    assert ingest.main(["publish"]) == 0
    capsys.readouterr()
    if problem == "extra":
        transport.tree.append(
            {
                "path": ingest.DATA_PATH + "secret.txt",
                "type": "blob",
                "mode": "100644",
                "sha": "f" * 40,
            }
        )
    elif problem == "mode":
        transport.tree[0]["mode"] = "120000"
    elif problem == "missing":
        transport.tree.pop()
    elif problem == "duplicate":
        transport.tree.append(deepcopy(transport.tree[0]))
    elif problem == "conflict":
        transport.tree[0]["sha"] = "f" * 40
    else:
        transport.problem = problem
    second = tmp_path / "new"
    second.mkdir(mode=0o700)
    monkeypatch.setenv("QUANT_CAPTURE_ROOT", str(second))
    monkeypatch.setattr(ingest, "_ROOT_BINDING", None)
    monkeypatch.delenv("MASSIVE_API_KEY")
    assert ingest.main(["capture"]) == 2
    assert not any(url.startswith("https://api.massive.com") for _, url, _ in transport.calls)


@pytest.mark.parametrize(
    "change",
    [
        "trust",
        "clocks",
        "extra",
        "file_extra",
        "symbol",
        "kind",
        "urls",
        "encoder",
        "future",
        "size",
        "payload",
    ],
)
def test_tampered_manifest_or_payload_cannot_be_uploaded(runtime, change):
    root, transport = runtime
    bundle(root)
    path = root / "upload/manifest.json"
    data = json.loads(path.read_bytes())
    if change == "trust":
        data["trusted_entry"] = True
    elif change == "clocks":
        data["clocks"]["published_at"] = AS_OF.isoformat()
    elif change == "extra":
        data["private_path"] = "/DO_NOT_PRINT"
    elif change == "file_extra":
        data["files"]["raw.csv"]["private_path"] = "/DO_NOT_PRINT"
    elif change == "symbol":
        data["symbol"] = "OTHER"
    elif change == "kind":
        data["source_kind"] = "certified"
    elif change == "urls":
        data["request_urls"]["raw"] += "&apiKey=DO_NOT_PRINT"
    elif change == "encoder":
        data["encoder"] = "other"
    elif change == "future":
        data["observed_at"] = "2099-01-01T00:00:00+00:00"
    elif change == "size":
        data["files"]["raw.csv"]["size_bytes"] = True
    elif change == "payload":
        (root / "upload/raw.csv").write_bytes(b"DO_NOT_PRINT")
    path.write_bytes(canonical(data))
    with pytest.raises((DemandError, ValueError)):
        ingest.publish(root, ingest.GitHub())
    assert not transport.blobs


@pytest.mark.parametrize(
    "change", ["short", "timestamp", "schema", "bool", "nan", "negative", "ohlc"]
)
def test_structural_row_validation_is_strict(change):
    data = rows()
    if change == "short":
        data.pop()
    elif change == "timestamp":
        data[1]["timestamp_ms"] = data[0]["timestamp_ms"]
    elif change == "schema":
        data[0]["extra"] = 1
    elif change == "bool":
        data[0]["open"] = True
    elif change == "nan":
        data[0]["volume"] = float("nan")
    elif change == "negative":
        data[0]["volume"] = -1
    elif change == "ohlc":
        data[0]["high"] = 1
    with pytest.raises(DemandError):
        ingest._rows(data)


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"x" * 524289,
        b"bad\n1\n",
        b"t,t,o,h,l,c,v\n1,1,1,1,1,1,1\n",
        b"timestamp_ms,open,high,low,close,volume\n1,2,3,4,5,6,7\n",
        b"timestamp_ms,open,high,low,close,volume\n1,2\n",
    ],
    ids=["empty", "oversize", "schema", "duplicate_header", "extra_cell", "missing_cell"],
)
def test_csv_input_bounds_and_schema(content):
    with pytest.raises((DemandError, ValueError)):
        ingest.csv_rows(content)


def test_root_identity_drift_and_missing_root_stop_before_transport(runtime, monkeypatch):
    root, transport = runtime
    assert ingest.guarded_root() == root
    monkeypatch.setattr(ingest, "_root_details", lambda _root: {"changed": True})
    with pytest.raises(DemandError, match="PRIVATE_ROOT_CHANGED"):
        ingest.guarded_root()
    monkeypatch.setenv("QUANT_CAPTURE_ROOT", "")
    with pytest.raises(DemandError, match="PRIVATE_ROOT_REQUIRED"):
        ingest.guarded_root()
    assert transport.calls == []


def test_vendor_csv_normalizes_without_promoting_source():
    csv_bytes, _ = ingest._encode(rows())
    vendor = csv_bytes.replace(b"timestamp_ms,open,high,low,close,volume", b"t,o,h,l,c,v")
    assert ingest.csv_rows(vendor) == rows()


def test_invalid_metadata_and_sha_never_accept_strings_as_proofs():
    with pytest.raises(DemandError):
        ingest._json(b"[]")
    with pytest.raises(DemandError):
        ingest._sha(True)


def test_reservations_persist_and_never_reset_eight_attempt_budget(runtime):
    _, transport = runtime
    github = ingest.GitHub()
    assert [github.reserve_pair() for _ in range(4)] == [0, 1, 2, 3]
    with pytest.raises(DemandError, match="CAPTURE_BUDGET_EXHAUSTED"):
        github.reserve_pair()
    assert len(transport.reservations) == 4
    assert not any(url.startswith("https://api.massive.com") for _, url, _ in transport.calls)


@pytest.mark.parametrize("change", ["request_id", "otc", "n", "vw", "partial_next", "empty"])
def test_free_text_and_unsupported_source_metadata_stop_before_archive(change):
    data = provider_page(False)
    if change == "request_id":
        data["request_id"] = "/DO_NOT_PRINT"
    elif change == "otc":
        data["results"][0]["otc"] = "PRIVATE"
    elif change == "n":
        data["results"][0]["n"] = True
    elif change == "vw":
        data["results"][0]["vw"] = {"private_path": "DO_NOT_PRINT"}
    elif change == "empty":
        with pytest.raises(DemandError):
            ingest.source_rows(b"", "raw")
        return
    else:
        data["results"] = data["results"][:2]
        data["resultsCount"] = data["queryCount"] = 2
        plan = ingest.decode_demand_plan(ingest.fixed_plan(), AS_OF)
        data["next_url"] = ingest.fixed_url(plan, "raw").replace("1791552600000", "1791552720000")
    with pytest.raises(DemandError):
        ingest.source_rows(canonical(data), "raw")


def test_integer_prices_optional_source_fields_and_duplicate_metadata():
    data = provider_page(False)
    for row in data["results"]:
        row.update(o=100, h=101, l=99, c=100, n=5, vw=100.0, otc=False)
    result = ingest.source_rows(canonical(data), "raw")
    csv_bytes, _ = ingest._encode(result)
    assert ingest._encode(ingest.csv_rows(csv_bytes))[0] == csv_bytes
    with pytest.raises(DemandError, match="METADATA_INVALID"):
        ingest._json(b'{"observed_at":"/PRIVATE","observed_at":"2026"}')


@pytest.mark.parametrize(
    "change", ["format", "actual_size", "declared_size", "source", "parquet", "checks", "budget"]
)
def test_rebound_payload_or_noncanonical_manifest_is_still_rejected(runtime, monkeypatch, change):
    root, _ = runtime
    bundle(root)
    path = root / "upload/manifest.json"
    manifest = json.loads(path.read_bytes())
    if change == "format":
        path.write_bytes(json.dumps(manifest, indent=2).encode())
    elif change == "actual_size":
        (root / "upload/raw.csv").write_bytes(b"x" * (ingest.FILE_LIMIT + 1))
    elif change == "declared_size":
        manifest["files"]["raw.csv"]["size_bytes"] += 1
        path.write_bytes(canonical(manifest))
    elif change == "source":
        manifest["source_sha256"]["raw"] = "f" * 64
        path.write_bytes(canonical(manifest))
    elif change == "budget":
        monkeypatch.setattr(ingest, "TOTAL_LIMIT", 1)
    else:
        name = "raw.parquet" if change == "parquet" else "checks.json"
        body = b"PRIVATE_METADATA" if change == "parquet" else canonical({"trusted_entry": 0})
        (root / "upload" / name).write_bytes(body)
        manifest["files"][name] = {"sha256": digest(body), "size_bytes": len(body)}
        path.write_bytes(canonical(manifest))
    with pytest.raises(DemandError):
        ingest.load_bundle(root)


@pytest.mark.parametrize(
    "status,body",
    [(500, b"PRIVATE"), (200, b" " * 1048577), (200, b"[]")],
    ids=["status", "oversize", "schema"],
)
def test_github_transport_response_limits_are_enforced(runtime, monkeypatch, status, body):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            return body[:size]

    response = Response()
    response.status = status
    monkeypatch.setattr(
        ingest,
        "build_opener",
        lambda *_args: type("Opener", (), {"open": lambda _self, *_args, **_kwargs: response})(),
    )
    with pytest.raises(DemandError):
        ingest.GitHub().private()


def test_missing_provider_key_stops_before_reservation(runtime, monkeypatch, capsys):
    _, transport = runtime
    monkeypatch.delenv("MASSIVE_API_KEY")
    assert ingest.main(["capture"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "CREDENTIAL_UNAVAILABLE"
    assert not transport.reservations


def test_all_parameter_ids_fit_windows_environment(request):
    assert (
        max(
            len(item.nodeid)
            for item in request.session.items
            if item.path == Path(__file__).resolve()
        )
        < 1024
    )
