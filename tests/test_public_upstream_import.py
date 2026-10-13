"""Validate the public-only importer without any network, archive or Git writes."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from tools import public_upstream_import as importer


class PublicImportTests(unittest.TestCase):
    def env(self, temporary):
        return {
            "GITHUB_REPOSITORY": importer.REPOSITORY,
            "GITHUB_ACTOR": "dczhhh",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_SHA": "a" * 40,
            "PUBLIC_UPSTREAM_IMPORT_REVIEWED_SHA": "a" * 40,
            "PUBLIC_UPSTREAM_IMPORT_ENABLED": "true",
            "RUNNER_ENVIRONMENT": "github-hosted",
            "RUNNER_TEMP": temporary,
            "GITHUB_TOKEN": "INVENTED_CURRENT_REPO_TOKEN",
        }

    def test_owner_public_import_has_clean_parent_and_create_only_ref(self):
        events = []
        pushed = False

        def git(*args, env=None):
            nonlocal pushed
            events.append(args)
            if args[:2] == ("rev-parse", "HEAD"):
                return b"a" * 40 + b"\n"
            if args[:2] == ("remote", "get-url"):
                return ("https://github.com/" + importer.REPOSITORY + ".git\n").encode()
            if args[:2] == ("rev-parse", "FETCH_HEAD"):
                return importer.REVISION.encode() + b"\n"
            if args[:3] == ("ls-tree", "-rz", "a" * 40):
                return b"".join(
                    b"100644 blob " + b"b" * 40 + b"\t" + path.encode() + b"\0"
                    for path in (
                        ".github/workflows/ci.yml",
                        "tests/__init__.py",
                        "tests/test_public_upstream_import.py",
                        "tests/test_private_archive_migration.py",
                    )
                )
            if args[:3] == ("ls-tree", "-rz", "--name-only"):
                return b".github/workflows/legacy-release.yml\0"
            if args[0] == "write-tree":
                return b"c" * 40 + b"\n"
            if args[0] == "ls-remote":
                return (b"d" * 40 + b"\trefs/heads/" + importer.BRANCH.encode()) if pushed else b""
            if args[0] == "commit-tree":
                self.assertEqual(args[2:4], ("-p", "a" * 40))
                return b"d" * 40 + b"\n"
            if "push" in args:
                self.assertIn("--force-with-lease=refs/heads/" + importer.BRANCH + ":", args)
                self.assertEqual(args[-1], "d" * 40 + ":refs/heads/" + importer.BRANCH)
                self.assertNotIn("--all", args)
                pushed = True
            return b""

        body = json.dumps({"full_name": importer.REPOSITORY, "private": False}).encode()
        output = io.StringIO()
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(os.environ, self.env(temporary), clear=True),
            patch.object(importer, "git", side_effect=git),
            patch.object(importer, "build_opener") as opener,
            redirect_stdout(output),
        ):
            opener.return_value.open.return_value = io.BytesIO(body)
            self.assertEqual(importer.main(), 0)
        self.assertTrue(pushed)
        self.assertIn(
            ("fetch", "--no-tags", "--depth=1", importer.SOURCE, importer.REVISION), events
        )
        self.assertIn(
            ("update-index", "--force-remove", ".github/workflows/legacy-release.yml"), events
        )
        for path in (
            "tests/__init__.py",
            "tests/test_public_upstream_import.py",
            "tests/test_private_archive_migration.py",
        ):
            self.assertIn(
                ("update-index", "--add", "--cacheinfo", "100644", "b" * 40, path), events
            )
        self.assertEqual(
            json.loads(output.getvalue()),
            {"status": "PUBLIC_UPSTREAM_BASELINE_IMPORTED", "private_archive_reads": 0},
        )
        self.assertNotIn("INVENTED_CURRENT_REPO_TOKEN", output.getvalue())

    def test_wrong_owner_ref_sha_enable_and_repository_stop_before_network(self):
        for key, value in [
            ("GITHUB_ACTOR", "invented-other"),
            ("GITHUB_REF", "refs/pull/1/merge"),
            ("GITHUB_SHA", "b" * 40),
            ("GITHUB_REPOSITORY", "invented/other"),
            ("PUBLIC_UPSTREAM_IMPORT_ENABLED", "false"),
            ("RUNNER_ENVIRONMENT", "self-hosted"),
        ]:
            with (
                self.subTest(key=key),
                tempfile.TemporaryDirectory() as temporary,
                patch.dict(os.environ, {**self.env(temporary), key: value}, clear=True),
                patch.object(importer, "build_opener") as opener,
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(importer.main(), 1)
                opener.assert_not_called()

    def test_private_repo_or_other_origin_cannot_import(self):
        for private, origin in [
            (True, "https://github.com/" + importer.REPOSITORY),
            (False, "https://invented.invalid/other"),
        ]:
            with self.subTest(private=private, origin=origin):
                output = io.StringIO()
                body = json.dumps({"full_name": importer.REPOSITORY, "private": private}).encode()
                with (
                    tempfile.TemporaryDirectory() as temporary,
                    patch.dict(os.environ, self.env(temporary), clear=True),
                    patch.object(
                        importer, "git", side_effect=[b"a" * 40 + b"\n", origin.encode()]
                    ) as git,
                    patch.object(importer, "build_opener") as opener,
                    redirect_stdout(output),
                ):
                    opener.return_value.open.return_value = io.BytesIO(body)
                    self.assertEqual(importer.main(), 1)
                    self.assertLessEqual(git.call_count, 2)
                self.assertIn("PUBLIC_UPSTREAM_IMPORT_STOPPED", output.getvalue())

    def test_public_log_redacts_private_exception(self):
        output = io.StringIO()
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(os.environ, self.env(temporary), clear=True),
            patch.object(importer, "git", side_effect=RuntimeError("INVENTED_TOKEN_DO_NOT_PRINT")),
            redirect_stdout(output),
        ):
            self.assertEqual(importer.main(), 1)
        self.assertNotIn("TOKEN_DO_NOT_PRINT", output.getvalue())

    def test_existing_baseline_is_read_only_and_bound_to_clean_parent(self):
        for parent, tree in [("a" * 40, "c" * 40), ("e" * 40, "c" * 40), ("a" * 40, "f" * 40)]:
            with self.subTest(parent=parent, tree=tree):
                events = []

                def git(*args, env=None, events=events, tree=tree, parent=parent):
                    events.append(args)
                    if args[:2] == ("rev-parse", "HEAD"):
                        return b"a" * 40
                    if args[:2] == ("remote", "get-url"):
                        return ("https://github.com/" + importer.REPOSITORY).encode()
                    if args[:2] == ("rev-parse", "FETCH_HEAD"):
                        return importer.REVISION.encode()
                    if args[:2] == ("rev-parse", "FETCH_HEAD^{tree}"):
                        return tree.encode()
                    if args[0] == "write-tree":
                        return b"c" * 40
                    if args[0] == "ls-remote":
                        return b"d" * 40 + b"\trefs/heads/" + importer.BRANCH.encode()
                    if args[0] == "rev-list":
                        return ("d" * 40 + " " + parent).encode()
                    return b""

                body = json.dumps({"full_name": importer.REPOSITORY, "private": False}).encode()
                with (
                    tempfile.TemporaryDirectory() as temporary,
                    patch.dict(os.environ, self.env(temporary), clear=True),
                    patch.object(importer, "git", side_effect=git),
                    patch.object(importer, "build_opener") as opener,
                    redirect_stdout(io.StringIO()),
                ):
                    opener.return_value.open.return_value = io.BytesIO(body)
                    self.assertEqual(
                        importer.main(), 0 if parent == "a" * 40 and tree == "c" * 40 else 1
                    )
                self.assertFalse(any("push" in args or "commit-tree" in args for args in events))


if __name__ == "__main__":
    unittest.main()
