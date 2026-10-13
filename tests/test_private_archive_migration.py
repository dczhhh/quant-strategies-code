"""Invented bytes and Git objects only; no private inventory or provider fixtures."""

import base64
import copy
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from tools import private_archive_migration as m


def identity(value):
    return m.git_blob(value.encode())


def invented_inventory():
    archives, payloads, references, commits, trees = [], {}, {}, {}, {}
    for i in range(22):
        rows = []
        for j in range(7 if i == 0 else 4):
            if i == 0 and j == 0:
                continue
            name = f"file_{j:02d}.parquet" if i == 0 and j == 6 else f"file_{j:02d}.json"
            path = f"research/data/invented_{i:02d}/" + name
            body = (
                b"INVENTED_BINARY\x00\x81\xff"
                if name.endswith("parquet")
                else m.canonical(
                    {"invented": True, "ordinal": [i, j], **dict.fromkeys(m.FLAGS, False)}
                )
            )
            payloads[path] = body
            rows.append(
                {
                    "path": path,
                    "mode": "100644",
                    "git_blob_sha": m.git_blob(body),
                    "size_bytes": len(body),
                }
            )
        if i == 0:
            path = "research/data/invented_00/manifest.json"
            body = m.canonical(
                {
                    "files": {
                        row["path"].rsplit("/", 1)[1]: {
                            "sha256": m.sha256(payloads[row["path"]]),
                            "size_bytes": row["size_bytes"],
                        }
                        for row in rows
                    },
                    **dict.fromkeys(m.FLAGS, False),
                }
            )
            payloads[path] = body
            rows.append(
                {
                    "path": path,
                    "mode": "100644",
                    "git_blob_sha": m.git_blob(body),
                    "size_bytes": len(body),
                }
            )
        commit, tree = identity(f"invented_commit_{i}"), identity(f"invented_tree_{i}")
        reference = f"research-data/invented-{i:02d}"
        references[reference] = commit
        commits[commit] = {"tree": {"sha": tree}, "parents": [{"sha": "1" * 40}]}
        trees[tree] = {
            "truncated": False,
            "tree": [
                {
                    "path": row["path"],
                    "type": "blob",
                    "mode": "100644",
                    "sha": row["git_blob_sha"],
                    "size": row["size_bytes"],
                }
                for row in rows
            ],
        }
        archives.append(
            {
                "source_ref": "refs/heads/" + reference,
                "source_commit": commit,
                "source_tree": tree,
                "source_parents": ["1" * 40],
                "files": rows,
                "status": "NOT_MIGRATED",
            }
        )
    reservations = []
    for i in range(26):
        reference = f"research-reservations/invented-{i:02d}"
        references[reference] = "2" * 40
        reservations.append(
            {
                "source_ref": "refs/heads/" + reference,
                "source_commit": "2" * 40,
                "status": "SOURCE_MARKER_PRESERVED_NOT_RESET",
            }
        )
    data = {
        "schema_version": "private_archive_migration_inventory_v1",
        "source_repository": m.SOURCE,
        "destination_repository": m.DESTINATION,
        "status": "INVENTORY_ONLY_NOT_MIGRATED",
        "archive_files_copied": 0,
        "allow_public_raw_data": False,
        "execution_admission": {
            **dict.fromkeys(m.FLAGS, False),
            "engine_invoked": False,
            "strategy_performance_computed": False,
        },
        "archives": archives,
        "reservations": reservations,
    }
    return data, payloads, references, commits, trees


class FakeGitHub(m.GitHub):
    def __init__(self, repository, payloads=None, refs=None, commits=None, trees=None):
        super().__init__(
            repository,
            "INVENTED_SOURCE_CREDENTIAL"
            if repository == m.SOURCE
            else "INVENTED_ARCHIVE_CREDENTIAL",
            writable=repository == m.DESTINATION,
        )
        self.private = True
        self.blobs = {m.git_blob(body): body for body in (payloads or {}).values()}
        self.refs = copy.deepcopy(refs or {})
        self.commits = copy.deepcopy(commits or {})
        self.trees = copy.deepcopy(trees or {})
        self.events = []
        self.before = None

    def request(self, method, suffix, payload=None):
        self.events.append((method, suffix))
        if self.before:
            self.before(method, suffix, payload)
        if suffix == "":
            return {
                "full_name": self.repository,
                "private": self.private,
                "visibility": "private" if self.private else "public",
            }
        if method == "GET":
            if suffix.startswith("/git/ref/heads/"):
                found = self.refs.get(suffix.removeprefix("/git/ref/heads/"))
                return None if found is None else {"object": {"sha": found}}
            if suffix.startswith("/git/blobs/"):
                body = self.blobs[suffix.removeprefix("/git/blobs/")]
                return {
                    "encoding": "base64",
                    "content": base64.b64encode(body).decode(),
                    "size": len(body),
                }
            if suffix.startswith("/git/commits/"):
                return self.commits[suffix.removeprefix("/git/commits/")]
            if suffix.startswith("/git/trees/"):
                return self.trees[suffix.removeprefix("/git/trees/").removesuffix("?recursive=1")]
        m.require(method == "POST" and self.repository == m.DESTINATION)
        if suffix == "/git/blobs":
            body = base64.b64decode(payload["content"], validate=True)
            result = m.git_blob(body)
            self.blobs[result] = body
        elif suffix == "/git/trees":
            result = m.git_blob(m.canonical(payload))
            self.trees[result] = {
                "truncated": False,
                "tree": [{**row, "size": len(self.blobs[row["sha"]])} for row in payload["tree"]],
            }
        elif suffix == "/git/commits":
            result = m.git_blob(m.canonical(payload))
            self.commits[result] = {
                "tree": {"sha": payload["tree"]},
                "parents": [{"sha": parent} for parent in payload["parents"]],
            }
        elif suffix == "/git/refs":
            name = payload["ref"].removeprefix("refs/heads/")
            m.require(name not in self.refs)
            self.refs[name] = payload["sha"]
            result = payload["sha"]
        else:
            raise AssertionError("Unexpected route")
        return {"sha": result}


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.inventory, self.payloads, refs, commits, trees = invented_inventory()
        self.content = m.canonical(self.inventory)
        self.pin = patch.object(m, "INVENTORY_BLOB", m.git_blob(self.content))
        self.pin.start()
        self.addCleanup(self.pin.stop)
        self.source = FakeGitHub(m.SOURCE, self.payloads, refs, commits, trees)
        self.destination = FakeGitHub(m.DESTINATION)
        self.destination.blobs[m.INVENTORY_BLOB] = self.content
        initial_tree = identity("invented_bootstrap_tree")
        self.destination.commits[m.BOOTSTRAP] = {
            "tree": {"sha": initial_tree},
            "parents": [{"sha": "9" * 40}],
        }
        self.destination.trees[initial_tree] = {
            "truncated": False,
            "tree": [
                {
                    "path": m.INVENTORY_PATH,
                    "sha": m.INVENTORY_BLOB,
                    "type": "blob",
                    "mode": "100644",
                }
            ],
        }

    def migrated_tree(self):
        commit = self.destination.commits[self.destination.refs[m.MIGRATION_REF]]
        return self.destination.trees[commit["tree"]["sha"]]

    def test_exact_binary_preserving_copy_and_independent_readback(self):
        self.assertEqual(m.inventory_bytes(self.destination), self.content)
        before = copy.deepcopy(self.source.refs)
        m.migrate(self.source, self.destination, self.content)
        m.verify(self.destination, self.content)
        self.assertEqual(before, self.source.refs)
        self.assertEqual(len(self.migrated_tree()["tree"]), 93)
        self.assertTrue(
            all(self.destination.blobs[m.git_blob(body)] == body for body in self.payloads.values())
        )
        self.assertFalse(any(method != "GET" for method, _ in self.source.events))
        for i, event in enumerate(self.destination.events):
            if event[0] == "POST":
                self.assertEqual(self.destination.events[i - 1], ("GET", ""))

    def test_fresh_runner_replay_has_no_writes_or_source_payload_download(self):
        m.migrate(self.source, self.destination, self.content)
        before = copy.deepcopy(self.destination.refs)
        self.source.events.clear()
        self.destination.events.clear()
        m.migrate(self.source, self.destination, self.content)
        self.assertEqual(before, self.destination.refs)
        self.assertFalse(any(method != "GET" for method, _ in self.destination.events))
        self.assertFalse(any(suffix.startswith("/git/blobs/") for _, suffix in self.source.events))

    def test_source_marker_or_archive_reference_change_stops_before_writes(self):
        for name in [
            next(iter(self.source.refs)),
            self.inventory["reservations"][0]["source_ref"].removeprefix("refs/heads/"),
        ]:
            with self.subTest(name=name):
                original = self.source.refs[name]
                self.source.refs[name] = "f" * 40
                with self.assertRaises(m.Stopped):
                    m.migrate(self.source, self.destination, self.content)
                self.source.refs[name] = original
        self.assertFalse(any(method == "POST" for method, _ in self.destination.events))

    def test_private_source_and_destination_are_both_required(self):
        for client in (self.source, self.destination):
            with self.subTest(repository=client.repository):
                client.private = False
                with self.assertRaises(m.Stopped):
                    m.migrate(self.source, self.destination, self.content)
                client.private = True
        self.assertFalse(any(method == "POST" for method, _ in self.destination.events))

    def test_destination_visibility_change_blocks_the_next_write(self):
        def change(method, suffix, _):
            if method == "POST" and suffix == "/git/blobs":
                self.destination.private = False

        self.destination.before = change
        with self.assertRaises(m.Stopped):
            m.migrate(self.source, self.destination, self.content)
        self.assertEqual(sum(method == "POST" for method, _ in self.destination.events), 1)
        self.assertNotIn(m.MIGRATION_REF, self.destination.refs)

    def test_corrupt_source_payload_never_gets_written(self):
        self.source.blobs[next(iter(self.source.blobs))] = b"INVENTED_CORRUPTION"
        with self.assertRaises(m.Stopped):
            m.migrate(self.source, self.destination, self.content)
        self.assertFalse(any(method == "POST" for method, _ in self.destination.events))

    def test_source_tree_binding_and_parent_identity_are_required(self):
        for field, value in [("tree", {"sha": "f" * 40}), ("parents", [])]:
            with self.subTest(field=field):
                first = self.source.commits[self.inventory["archives"][0]["source_commit"]]
                original = first[field]
                first[field] = value
                with self.assertRaises(m.Stopped):
                    m.migrate(self.source, self.destination, self.content)
                first[field] = original
        self.assertFalse(any(method == "POST" for method, _ in self.destination.events))

    def test_extra_files_wrong_mode_wrong_parent_and_truncation_fail_readback(self):
        m.migrate(self.source, self.destination, self.content)
        tree = self.migrated_tree()
        for mutation in ("extra", "mode", "parent", "truncated"):
            with self.subTest(mutation=mutation):
                original_tree = copy.deepcopy(tree)
                commit = self.destination.commits[self.destination.refs[m.MIGRATION_REF]]
                original_parents = copy.deepcopy(commit["parents"])
                if mutation == "extra":
                    tree["tree"].append(
                        {
                            "path": "UNAPPROVED_FILE",
                            "type": "blob",
                            "mode": "100644",
                            "sha": "f" * 40,
                        }
                    )
                elif mutation == "mode":
                    tree["tree"][0]["mode"] = "120000"
                elif mutation == "parent":
                    commit["parents"] = [{"sha": "f" * 40}]
                else:
                    tree["truncated"] = True
                with self.assertRaises(m.Stopped):
                    m.verify(self.destination, self.content)
                tree.clear()
                tree.update(original_tree)
                commit["parents"] = original_parents

    def test_corrupt_destination_payload_and_mapping_fail_readback(self):
        m.migrate(self.source, self.destination, self.content)
        for path in [next(iter(self.payloads)), m.MAP_PATH]:
            with self.subTest(path=path):
                row = next(row for row in self.migrated_tree()["tree"] if row["path"] == path)
                original = row["sha"]
                changed = b"INVENTED_DIFFERENT_BYTES"
                self.destination.blobs[m.git_blob(changed)] = changed
                row["sha"] = m.git_blob(changed)
                with self.assertRaises(m.Stopped):
                    m.verify(self.destination, self.content)
                row["sha"] = original

    def test_reviewed_inventory_pin_cannot_be_replaced(self):
        self.destination.blobs[m.INVENTORY_BLOB] = self.content + b" "
        with self.assertRaises(m.Stopped):
            m.inventory_bytes(self.destination)

    def test_original_manifest_sha256_and_receipt_binding_are_verified(self):
        value = copy.deepcopy(self.payloads)
        name = "research/data/invented_00/manifest.json"
        record = json.loads(value[name])
        record["files"]["file_01.json"]["sha256"] = "f" * 64
        value[name] = m.canonical(record)
        with self.assertRaises(m.Stopped):
            m.validate_metadata(self.inventory, value)
        name = "research/sources/invented/receipt.json"
        response = b"INVENTED_RESPONSE"
        value = {
            **self.payloads,
            "research/sources/invented/response.json": response,
            name: m.canonical(
                {
                    "source_sha256": m.sha256(response),
                    "size_bytes": len(response),
                    **dict.fromkeys(m.FLAGS, False),
                }
            ),
        }
        m.validate_metadata(self.inventory, value)
        value[name] = m.canonical(
            {
                "source_sha256": "f" * 64,
                "size_bytes": len(response),
                **dict.fromkeys(m.FLAGS, False),
            }
        )
        with self.assertRaises(m.Stopped):
            m.validate_metadata(self.inventory, value)

    def test_inventory_scope_types_bounds_and_trust_cannot_expand(self):
        for mutation in (
            "count",
            "duplicate",
            "path",
            "boolean_size",
            "large",
            "trust",
            "public",
            "reservation",
        ):
            with self.subTest(mutation=mutation):
                value = copy.deepcopy(self.inventory)
                row = value["archives"][0]["files"][0]
                if mutation == "count":
                    value["archives"].pop()
                elif mutation == "duplicate":
                    value["archives"][0]["files"][1]["path"] = row["path"]
                elif mutation == "path":
                    row["path"] = "research/data/../ESCAPE"
                elif mutation == "boolean_size":
                    row["size_bytes"] = True
                elif mutation == "large":
                    row["size_bytes"] = m.FILE_LIMIT + 1
                elif mutation == "trust":
                    value["execution_admission"]["may_build_engine"] = True
                elif mutation == "public":
                    value["allow_public_raw_data"] = True
                else:
                    value["reservations"][0]["status"] = "RESET"
                with self.assertRaises(m.Stopped):
                    m.source_files(value)

    def test_read_credential_cannot_write_or_change_destination(self):
        with self.assertRaises(m.Stopped):
            self.source.write("/git/blobs", {})
        for repository in (m.SOURCE, m.CODE, "invented/other"):
            with self.subTest(repository=repository), self.assertRaises(m.Stopped):
                m.GitHub(repository, "INVENTED_KEY", writable=True)

    def test_live_client_rejects_unlisted_methods_routes_and_call_overrun(self):
        client = m.GitHub(m.DESTINATION, "INVENTED_KEY", writable=True)
        for method, route in [
            ("PATCH", "/git/refs"),
            ("DELETE", "/git/refs"),
            ("GET", "/secrets"),
            ("GET", "https://invented.invalid"),
            ("POST", "/git/refs/heads/main"),
        ]:
            with self.subTest(method=method, route=route), self.assertRaises(m.Stopped):
                client.request(method, route, {})
        with patch.object(m, "CALL_LIMIT", 0), self.assertRaises(m.Stopped):
            client.request("GET", "")

    def test_redirects_are_rejected_without_following_credentials(self):
        with self.assertRaises(m.Stopped):
            m.NoRedirect().redirect_request(
                None, None, 302, "invented", {}, "https://invented.invalid"
            )

    def test_live_transport_keeps_each_credential_on_its_repository(self):
        class Response(io.BytesIO):
            status = 200

        requests = []

        class Opener:
            def open(self, request, timeout):
                requests.append((request, timeout))
                return Response(b'{"invented":true}')

        with patch.object(m, "build_opener", return_value=Opener()):
            m.GitHub(m.SOURCE, "INVENTED_READ_KEY").request("GET", "")
            m.GitHub(m.DESTINATION, "INVENTED_WRITE_KEY").request("GET", "")
            m.GitHub(m.CODE).request("GET", "")
        self.assertEqual(
            [r.full_url for r, _ in requests],
            ["https://api.github.com/repos/" + repo for repo in (m.SOURCE, m.DESTINATION, m.CODE)],
        )
        self.assertEqual(
            [r.get_header("Authorization") for r, _ in requests],
            ["Bearer INVENTED_READ_KEY", "Bearer INVENTED_WRITE_KEY", None],
        )
        self.assertTrue(all(timeout == 20 for _, timeout in requests))

    def test_only_absent_git_ref_is_a_recoverable_http_404(self):
        for suffix, code in [
            ("/git/ref/heads/research-migrations/invented", 404),
            ("/git/blobs/" + "a" * 40, 404),
            ("", 403),
            ("", 429),
            ("", 500),
        ]:
            with self.subTest(suffix=suffix, code=code):
                error = HTTPError(
                    "https://api.github.com/invented",
                    code,
                    "INVENTED_PRIVATE_ERROR",
                    {},
                    io.BytesIO(),
                )
                with patch.object(m, "build_opener") as opener:
                    opener.return_value.open.side_effect = error
                    client = m.GitHub(m.DESTINATION, "INVENTED_KEY")
                    if suffix.startswith("/git/ref/") and code == 404:
                        self.assertIsNone(client.request("GET", suffix))
                    else:
                        with self.assertRaises(m.Stopped):
                            client.request("GET", suffix)
                    self.assertEqual(opener.return_value.open.call_count, 1)

    def test_live_transport_bounds_json_and_status_before_use(self):
        class Response(io.BytesIO):
            status = 200

        for body, status in [(b"[]", 200), (b"x" * (m.RESPONSE_LIMIT + 1), 200), (b"{}", 302)]:
            with self.subTest(size=len(body), status=status):
                response = Response(body)
                response.status = status
                with patch.object(m, "build_opener") as opener, self.assertRaises(m.Stopped):
                    opener.return_value.open.return_value = response
                    m.GitHub(m.DESTINATION, "INVENTED_KEY").request("GET", "")

    def test_source_metadata_change_during_upload_never_publishes_a_ref(self):
        def change(method, suffix, _):
            if method == "POST" and suffix == "/git/commits":
                self.source.refs[next(iter(self.source.refs))] = "f" * 40

        self.destination.before = change
        with self.assertRaises(m.Stopped):
            m.migrate(self.source, self.destination, self.content)
        self.assertNotIn(m.MIGRATION_REF, self.destination.refs)

    def test_original_raw_and_separate_source_binding_sha256_are_checked(self):
        response = b"INVENTED_RAW_RESPONSE"
        receipt = m.canonical(
            {
                "source_sha256": m.sha256(response),
                "size_bytes": len(response),
                **dict.fromkeys(m.FLAGS, False),
            }
        )
        paths = {
            "research/sources/invented/raw/response.json": response,
            "research/sources/invented/raw/receipt.json": receipt,
            "research/data/invented/raw_response.json": response,
            "research/data/invented/comparison_response.json": response,
        }
        source_commit = "e" * 40
        inventory = {
            "archives": [
                {
                    "source_commit": source_commit,
                    "files": [
                        {"path": path} for path in paths if path.startswith("research/sources/")
                    ],
                }
            ]
        }
        manifest = {
            "files": {
                "raw_response.json": {"sha256": m.sha256(response), "size_bytes": len(response)}
            },
            "source_sha256": {"raw": m.sha256(response), "comparison": m.sha256(response)},
            "source_bindings": {
                "raw": {
                    "source_commit": source_commit,
                    "receipt_sha256": m.sha256(receipt),
                    "response_sha256": m.sha256(response),
                }
            },
            **dict.fromkeys(m.FLAGS, False),
        }
        paths["research/data/invented/manifest.json"] = m.canonical(manifest)
        m.validate_metadata(inventory, paths)
        for kind in ("raw", "binding"):
            changed = copy.deepcopy(manifest)
            if kind == "raw":
                changed["source_sha256"]["raw"] = "f" * 64
            else:
                changed["source_bindings"]["raw"]["receipt_sha256"] = "f" * 64
            paths["research/data/invented/manifest.json"] = m.canonical(changed)
            with self.subTest(kind=kind), self.assertRaises(m.Stopped):
                m.validate_metadata(inventory, paths)

    def test_guard_accepts_only_owner_manual_public_main_and_pinned_sha(self):
        env = {
            "GITHUB_ACTIONS": "true",
            "RUNNER_ENVIRONMENT": "github-hosted",
            "GITHUB_REPOSITORY": m.CODE,
            "GITHUB_ACTOR": "dczhhh",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_SHA": "a" * 40,
            "ARCHIVE_MIGRATION_REVIEWED_SHA": "a" * 40,
            "ARCHIVE_MIGRATION_ENABLED": "true",
        }
        with patch.dict(os.environ, env, clear=True), patch.object(m.GitHub, "visibility") as check:
            m.guarded()
            check.assert_called_once_with(False)
        for key, bad in [
            ("GITHUB_ACTIONS", "false"),
            ("RUNNER_ENVIRONMENT", "self-hosted"),
            ("GITHUB_REPOSITORY", m.SOURCE),
            ("GITHUB_ACTOR", "invented_other"),
            ("GITHUB_EVENT_NAME", "pull_request_target"),
            ("GITHUB_REF", "refs/pull/1/merge"),
            ("GITHUB_SHA", "b" * 40),
            ("ARCHIVE_MIGRATION_REVIEWED_SHA", "not-a-sha"),
            ("ARCHIVE_MIGRATION_ENABLED", "false"),
            ("ACTIONS_STEP_DEBUG", "true"),
            ("ACTIONS_RUNNER_DEBUG", "TRUE"),
        ]:
            with (
                self.subTest(key=key),
                patch.dict(os.environ, {**env, key: bad}, clear=True),
                patch.object(m.GitHub, "visibility"),
                self.assertRaises(m.Stopped),
            ):
                m.guarded()

    def test_public_status_never_prints_underlying_private_exception(self):
        output = io.StringIO()
        with (
            patch.object(m.sys, "argv", ["migration", "migrate"]),
            patch.object(m, "guarded", side_effect=ValueError("INVENTED_PRIVATE_SECRET")),
            redirect_stdout(output),
        ):
            self.assertEqual(m.main(), 1)
        self.assertNotIn("PRIVATE_SECRET", output.getvalue())
        self.assertEqual(
            json.loads(output.getvalue()), {"status": "MIGRATION_STOPPED", "vendor_requests": 0}
        )

    def test_public_ci_and_privileged_job_have_separate_credentials(self):
        root = Path(__file__).resolve().parents[1]
        ci = (root / ".github/workflows/ci.yml").read_text()
        privileged = (root / ".github/workflows/archive-migration.yml").read_text()
        self.assertNotIn("secrets.", ci)
        self.assertIn("environment: private-archive", privileged)
        self.assertNotIn("pull_request", privileged)
        self.assertNotIn("workflow_run", privileged)
        self.assertNotIn("workflow_call", privileged)
        self.assertEqual(privileged.count("secrets.LEGACY_SOURCE_READ_TOKEN"), 1)
        for text in (ci, privileged):
            self.assertNotIn("upload-artifact", text)
            self.assertNotIn("actions/cache", text)
            self.assertNotIn("secrets.massive", text)
            self.assertIn("persist-credentials: false", text)


if __name__ == "__main__":
    unittest.main()
