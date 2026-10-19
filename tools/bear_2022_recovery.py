"""Owner-approved missing-input recovery; immutable old refs, no Engine or SEC calls."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import bear_2022_market as m  # noqa: E402

s, c = m.s, m.c
ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = "config/research/bear_2022_recovery.json"
POLICY_BYTES = (ROOT / POLICY_PATH).read_bytes()
POLICY = s.decode(POLICY_BYTES)
PLAN_HASH = s.digest(POLICY_BYTES)
WORKFLOW = ".github/workflows/bear-2022-recovery.yml"
APPROVAL_REF = "research-data/bear-2022-market-recovery-v1-approval"
APPROVAL_PATH = "research/data/bear_2022_market_recovery_v1/approval.json"


def replacement(name):
    pattern = r"research-(?:data|reservations)/bear-2022-market-v1-[a-z]+-[0-4]"
    s.need(re.fullmatch(pattern, name) is not None, "RECOVERY_REF_SCOPE")
    return name.replace("bear-2022-market-v1-", "bear-2022-market-recovery-v1-")


def retained(ticker, index):
    return m.ref(ticker, index) in POLICY["original_raw_refs"]


def missing_products(ticker):
    return tuple(
        p for i, group in enumerate(m.groups(ticker)) if not retained(ticker, i) for p in group
    )


def check_inventory(archive, ticker):
    s.need(POLICY["original_plan_sha256"] == m.PLAN_HASH, "RECOVERY_ORIGINAL_PLAN")
    for index in range(len(m.groups(ticker))):
        for reserved, pins in (
            (False, POLICY["original_raw_refs"]),
            (True, POLICY["original_reservation_refs"]),
        ):
            name = m.ref(ticker, index, reserved)
            s.need(archive.ref(name) == pins.get(name), "RECOVERY_ORIGINAL_REF_CHANGED")


class RecoveryArchive:
    """Redirect only missing groups to new refs, using the sealed v1 archive codec.

    Payload paths intentionally stay v1-compatible. An immutable approval binds
    old inventory and recovery scope. No original ref is created or modified.
    """

    def __init__(self, archive, ticker):
        s.need(ticker in s.TICKERS, "RECOVERY_TICKER_SCOPE")
        self.archive, self.token = archive, archive.token
        self.targets = {}
        for index in range(len(m.groups(ticker))):
            for reserved in (False, True):
                name = m.ref(ticker, index, reserved)
                self.targets[name] = name if retained(ticker, index) else replacement(name)

    def visibility(self, private):
        return self.archive.visibility(private)

    def ref(self, name):
        s.need(name in self.targets, "RECOVERY_REF_SCOPE")
        return self.archive.ref(self.targets[name])

    def request(self, method, suffix, payload=None):
        s.need(method == "GET" and payload is None, "RECOVERY_REQUEST_READ_ONLY")
        return self.archive.request(method, suffix)

    def blob(self, sha, size=None):
        return self.archive.blob(sha, size)

    def write(self, suffix, payload):
        if suffix == "/git/refs":
            original = payload["ref"].removeprefix("refs/heads/")
            target = self.targets.get(original)
            s.need(target is not None and target != original, "RECOVERY_ORIGINAL_WRITE_FORBIDDEN")
            payload = {**payload, "ref": "refs/heads/" + target}
        return self.archive.write(suffix, payload)


def approval(archive, *, verify_only):
    archive.visibility(True)
    payload = {APPROVAL_PATH: POLICY_BYTES}
    if archive.ref(APPROVAL_REF) is None:
        s.need(not verify_only, "RECOVERY_APPROVAL_MISSING")
        c.b.create(archive, APPROVAL_REF, payload, c.m.BOOTSTRAP)
    saved, parent = c.b.read(archive, APPROVAL_REF, set(payload))
    s.need(parent == c.m.BOOTSTRAP and saved == payload, "RECOVERY_APPROVAL_BYTES")


def capture(archive, ticker, source, key_factory, *, verify_only=False, get=m.fetch):
    approval(archive, verify_only=verify_only)
    check_inventory(archive, ticker)
    bodies = m.a.read_raw(archive, ticker)
    m.a.audit(archive, ticker, bodies, m.SOURCE_READER, verify_only=True)
    result, made = m.capture(
        RecoveryArchive(archive, ticker), ticker, source, key_factory,
        verify_only=verify_only, get=get,
    )
    check_inventory(archive, ticker)
    s.need(len(result) == 86 and made <= len(missing_products(ticker)), "RECOVERY_PRODUCT_BUDGET")
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
        and all(
            os.environ.get(k, "").lower() != "true"
            for k in ("ACTIONS_STEP_DEBUG", "ACTIONS_RUNNER_DEBUG")
        )
        and subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() == source,
        "RECOVERY_RUNNER_CONTEXT",
    )


def reason(error):
    code = getattr(error, "code", "")
    if code in {
        "CHECK_FAILED", "GITHUB_PRIMARY_RATE_LIMIT", "GITHUB_HTTP_401", "GITHUB_HTTP_403",
        "GITHUB_HTTP_404", "GITHUB_HTTP_429", "GITHUB_HTTP_FAILED",
    }:
        return "BEAR2022_RECOVERY_" + code
    message = str(error)
    return message if re.fullmatch(r"BEAR2022_[A-Z_0-9]+", message) else "BEAR2022_RECOVERY_UNVERIFIED"


def main():
    try:
        s.need(len(sys.argv) == 4 and sys.argv[1] in {"capture", "verify"}, "RECOVERY_ARGUMENTS")
        mode, ticker, source = sys.argv[1:]
        guarded(source)
        archive = c.r.CachedArchive(c.PacedGitHub(
            c.m.DESTINATION, os.environ.get("ARCHIVE_WRITE_TOKEN", ""), writable=mode == "capture",
        ))
        _, made = capture(
            archive, ticker, source, lambda: os.environ.get("MASSIVE_API_KEY", ""),
            verify_only=mode == "verify",
        )
        print(json.dumps({
            "status": "BEAR2022_RECOVERY_RETAINED_NOT_ECONOMIC_ADMISSION", "ticker": ticker,
            "new_market_requests": made, "products": 86, "recovery_plan_sha256": PLAN_HASH,
            "original_refs_unchanged": True, "engine_invoked": False, "PIT_verified": False,
        }, sort_keys=True))
        return 0
    except Exception as error:
        print(json.dumps({"status": "BLOCKED", "reason": reason(error), "engine_invoked": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
