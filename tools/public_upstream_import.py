"""Import only the reviewed PUBLIC upstream tree, with no private archive credential."""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from urllib.request import HTTPRedirectHandler, Request, build_opener

REPOSITORY = "dczhhh/quant-strategies-code"
SOURCE = "https://github.com/ml4t/backtest.git"
REVISION = "c1ee19c385db3cbad225822cf845832470694f2b"
BRANCH = "public-upstream-baseline-v1"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("redirect stopped")


def git(*args, env=None):
    return subprocess.run(
        ["git", *args], env=env, check=True, capture_output=True, timeout=300
    ).stdout


def main():
    try:
        expected = os.environ.get("PUBLIC_UPSTREAM_IMPORT_REVIEWED_SHA", "")
        assert len(expected) == 40 and all(c in "0123456789abcdef" for c in expected)
        assert os.environ.get("GITHUB_REPOSITORY") == REPOSITORY
        assert os.environ.get("GITHUB_ACTOR") == "dczhhh"
        assert os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        assert os.environ.get("GITHUB_REF") == "refs/heads/main"
        assert os.environ.get("GITHUB_SHA") == expected
        assert os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
        assert os.environ.get("PUBLIC_UPSTREAM_IMPORT_ENABLED") == "true"
        assert git("rev-parse", "HEAD").decode().strip() == expected
        request = Request(
            "https://api.github.com/repos/" + REPOSITORY,
            headers={"Accept": "application/vnd.github+json"},
        )
        with build_opener(NoRedirect()).open(request, timeout=20) as response:
            info = json.loads(response.read(65_537))
        assert info["full_name"] == REPOSITORY and info["private"] is False
        assert git("remote", "get-url", "origin").decode().strip() in {
            "https://github.com/" + REPOSITORY,
            "https://github.com/" + REPOSITORY + ".git",
        }
        current = git("ls-tree", "-rz", expected)
        git("fetch", "--no-tags", "--depth=1", SOURCE, REVISION)
        assert git("rev-parse", "FETCH_HEAD").decode().strip() == REVISION
        with tempfile.TemporaryDirectory(dir=os.environ["RUNNER_TEMP"]) as temporary:
            root = Path(temporary)
            askpass = root / "git-askpass"
            askpass.write_text(
                '#!/bin/sh\ncase "$1" in\n*Username*) printf "%s" "x-access-token" ;;\n*Password*) printf "%s" "$GITHUB_TOKEN" ;;\n*) exit 1 ;;\nesac\n'
            )
            askpass.chmod(0o700)
            env = {
                **os.environ,
                "GIT_INDEX_FILE": str(root / "public-index"),
                "GIT_ASKPASS": str(askpass),
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_AUTHOR_NAME": "dczhhh",
                "GIT_AUTHOR_EMAIL": "111127928+dczhhh@users.noreply.github.com",
                "GIT_COMMITTER_NAME": "dczhhh",
                "GIT_COMMITTER_EMAIL": "111127928+dczhhh@users.noreply.github.com",
            }
            git("read-tree", REVISION, env=env)
            paths = git("ls-tree", "-rz", "--name-only", REVISION, ".github/workflows").split(b"\0")
            for path in filter(None, paths):
                git("update-index", "--force-remove", path.decode(), env=env)
            for item in filter(None, current.split(b"\0")):
                attributes, raw_path = item.split(b"\t", 1)
                mode, kind, identity = attributes.decode().split()
                path = raw_path.decode()
                if (
                    path.startswith(".github/workflows/")
                    or path.startswith("tools/")
                    or path == "tests/test_private_archive_migration.py"
                ):
                    assert kind == "blob" and mode == "100644"
                    git("update-index", "--add", "--cacheinfo", mode, identity, path, env=env)
            tree = git("write-tree", env=env).decode().strip()
            existing = git("ls-remote", "origin", "refs/heads/" + BRANCH, env=env).decode().strip()
            if existing:
                git("fetch", "--no-tags", "origin", "refs/heads/" + BRANCH, env=env)
                assert git("rev-parse", "FETCH_HEAD^{tree}").decode().strip() == tree
            else:
                commit = (
                    git(
                        "commit-tree",
                        tree,
                        "-p",
                        expected,
                        "-m",
                        "Import reviewed public upstream tree without private history",
                        env=env,
                    )
                    .decode()
                    .strip()
                )
                git(
                    "-c",
                    "credential.helper=",
                    "push",
                    "--force-with-lease=refs/heads/" + BRANCH + ":",
                    "origin",
                    commit + ":refs/heads/" + BRANCH,
                    env=env,
                )
                assert (
                    git("ls-remote", "origin", "refs/heads/" + BRANCH, env=env)
                    .decode()
                    .startswith(commit)
                )
    except Exception:
        print('{"status":"PUBLIC_UPSTREAM_IMPORT_STOPPED","private_archive_reads":0}')
        return 1
    print('{"status":"PUBLIC_UPSTREAM_BASELINE_IMPORTED","private_archive_reads":0}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
