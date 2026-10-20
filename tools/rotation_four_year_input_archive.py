"""Hosted continuous-input qualification; a fresh verifier rebuilds all bytes."""

from __future__ import annotations

import gzip
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import rotation_four_year_inputs as a  # noqa: E402

c, u, need = a.c, a.u, a.need
PREFIX = "research/verification/rotation_four_year_inputs_v1/"
REF = "research-data/rotation-four-year-continuous-inputs-v1"
WORKFLOW = ".github/workflows/rotation-four-year-continuous-inputs.yml"
LIMITS = {"inputs.json": 64 * 1024 * 1024, "audit.json": 32 * 1024 * 1024}
MAX_PARTS = 32


class Factory:
    """Share only immutable Git objects; refs and visibility are checked live."""

    def __init__(self, token):
        self.token, self.objects, self.bodies = token, {}, {}

    def __call__(self, *, writable=False):
        result = c.r.CachedArchive(c.PacedGitHub(c.m.DESTINATION, self.token, writable=writable))
        result.objects, result.bodies = self.objects, self.bodies
        return result


def pack(logical, source):
    c.m.sha(source)
    need(set(logical) == set(LIMITS), "LOGICAL_SET")
    files, index = {}, {}
    for name, raw in sorted(logical.items()):
        need(isinstance(raw, bytes) and 0 < len(raw) <= LIMITS[name], "LOGICAL_EXPANSION")
        packed = gzip.compress(raw, mtime=0)
        parts = [packed[k : k + c.m.FILE_LIMIT] for k in range(0, len(packed), c.m.FILE_LIMIT)]
        need(0 < len(parts) <= MAX_PARTS, "COMPRESSED_PART_BUDGET")
        paths = []
        for k, body in enumerate(parts):
            path = PREFIX + name + f".gz.part-{k:02}.bin"
            files[path] = body
            paths.append(path)
        index[name] = {
            "parts": paths,
            "compressed_sha256": c.m.sha256(packed),
            "uncompressed_sha256": c.m.sha256(raw),
            "uncompressed_bytes": len(raw),
        }
    files[PREFIX + "manifest.json"] = u.canonical(
        {
            "schema": "rotation_four_year_continuous_input_manifest_v1",
            "source_commit": source,
            "capture_source": a.a.CAPTURE_SOURCE,
            "annual_candidate_commit": a.annual.COMMIT,
            "logical_files": index,
            "files": {p: c.m.sha256(b) for p, b in files.items()},
            "engine_invoked": False,
            "PIT_verified": False,
            "performance_admission": False,
        }
    )
    return files


def retain(archive, files, *, verify_only):
    archive.visibility(True)
    manifest_path = PREFIX + "manifest.json"
    need(manifest_path in files, "MANIFEST_MISSING")
    manifest = json.loads(files[manifest_path])
    need(set(manifest["logical_files"]) == set(LIMITS), "LOGICAL_SET")
    expected = {manifest_path}
    for name, item in manifest["logical_files"].items():
        paths = item["parts"]
        need(
            0 < len(paths) <= MAX_PARTS
            and paths == [PREFIX + name + f".gz.part-{k:02}.bin" for k in range(len(paths))],
            "PART_PATHS",
        )
        expected.update(paths)
    need(
        set(files) == expected and all(0 < len(b) <= c.m.FILE_LIMIT for b in files.values()),
        "ARCHIVE_SCOPE",
    )
    if archive.ref(REF) is None:
        need(not verify_only, "ARCHIVE_MISSING")
        c.b.create(archive, REF, files, c.m.BOOTSTRAP)
    saved, parent = c.b.read(archive, REF, set(files))
    need(parent == c.m.BOOTSTRAP and saved == files, "FRESH_INPUT_BYTES")
    return c.m.sha256(u.canonical({p: c.m.sha256(b) for p, b in sorted(files.items())}))


def guarded(source):
    c.m.sha(source)
    need(
        os.environ.get("GITHUB_REPOSITORY") == c.m.CODE
        and os.environ.get("GITHUB_ACTOR") == "dczhhh"
        and os.environ.get("GITHUB_EVENT_NAME") == "push"
        and os.environ.get("GITHUB_REF") == "refs/heads/main"
        and os.environ.get("GITHUB_WORKFLOW_REF") == c.m.CODE + "/" + WORKFLOW + "@refs/heads/main"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and os.environ.get("RUNNER_OS") == "Linux"
        and os.environ.get("ACTIONS_STEP_DEBUG", "").lower() != "true"
        and os.environ.get("ACTIONS_RUNNER_DEBUG", "").lower() != "true"
        and not os.environ.get("MASSIVE_API_KEY")
        and subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() == source,
        "HOSTED_CONTEXT",
    )


def main():
    try:
        need(len(sys.argv) == 3 and sys.argv[1] in {"run", "verify"}, "ARGUMENTS")
        mode, source = sys.argv[1:]
        guarded(source)
        factory = Factory(os.environ.get("ARCHIVE_WRITE_TOKEN", ""))
        _, normalized, audit = a.build(factory, source)
        files = pack({"inputs.json": normalized, "audit.json": u.canonical(audit)}, source)
        bundle = retain(factory(writable=mode == "run"), files, verify_only=mode == "verify")
        print(
            json.dumps(
                {
                    "status": "FOUR_YEAR_RETROSPECTIVE_INPUTS_REBUILT_AND_RETAINED",
                    "bundle_sha256": bundle,
                    "normalized_sha256": c.m.sha256(normalized),
                    "execution_sessions": 1003,
                    "new_vendor_requests": 0,
                    "new_official_requests": 0,
                    "only_assumed_distribution_years": [2022],
                    "annual_copy_provenance": a.annual.provenance(),
                    "engine_invoked": False,
                    "PIT_verified": False,
                    "performance_admission": False,
                }
            )
        )
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "FOUR_YEAR_CONTINUOUS_INPUTS_BLOCKED",
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
