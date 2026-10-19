"""Invented recovery archives only; original references must never be reset."""

import copy
import json

import pytest

from tests.test_private_archive_migration import FakeGitHub
from tools import bear_2022_recovery as r


def invented(monkeypatch, ticker="XLI", completed=4):
    archive = FakeGitHub(r.c.m.DESTINATION)
    body = b'{"status":"OK","results":[]}'
    r.m.capture(archive, ticker, "b" * 40, lambda: "INVENTED", get=lambda *_: (200, body))
    pins = {}
    raw = {}
    for index in range(5):
        name = r.m.ref(ticker, index)
        hold = r.m.ref(ticker, index, True)
        if index < completed:
            raw[name], pins[hold] = archive.ref(name), archive.ref(hold)
        else:
            del archive.refs[name]
            if index == completed:
                pins[hold] = archive.ref(hold)
            else:
                del archive.refs[hold]
    policy = dict(r.POLICY, original_raw_refs=raw, original_reservation_refs=pins)
    monkeypatch.setattr(r, "POLICY", policy)
    monkeypatch.setattr(r, "POLICY_BYTES", r.s.canonical(policy))
    monkeypatch.setattr(r.m.a, "read_raw", lambda *_: {})
    monkeypatch.setattr(r.m.a, "audit", lambda *_a, **_k: None)
    return archive, body


def test_frozen_scope_reuses_577_and_only_needs_455_products():
    assert sum(len(r.missing_products(t)) for t in r.s.TICKERS) == 455
    assert len(r.missing_products("XLI")) == 2
    assert len(r.missing_products("XLK")) == 23
    assert not r.missing_products("SPY")
    assert r.POLICY["original_refs_may_be_changed"] is False


@pytest.mark.parametrize("completed,expected", [(4, 2), (3, 23)])
def test_recovery_preserves_orphan_and_completed_refs_and_fresh_read_is_exact(monkeypatch, completed, expected):
    archive, body = invented(monkeypatch, completed=completed)
    before = copy.deepcopy(archive.refs)
    first, made = r.capture(archive, "XLI", "c" * 40, lambda: "INVENTED", get=lambda *_: (200, body))
    assert made == expected and len(first) == 86
    assert all(archive.ref(name) == sha for name, sha in before.items())
    after = copy.deepcopy(archive.refs)
    archive.events.clear()
    second, made = r.capture(archive, "XLI", "c" * 40, lambda: pytest.fail("key forbidden"), verify_only=True, get=lambda *_: pytest.fail("network forbidden"))
    assert made == 0 and first == second and archive.refs == after
    assert all(method == "GET" for method, _ in archive.events)


def test_changed_original_inventory_stops_before_market_request(monkeypatch):
    archive, _ = invented(monkeypatch)
    archive.refs[r.m.ref("XLI", 0)] = "f" * 40
    with pytest.raises(ValueError, match="ORIGINAL_REF_CHANGED"):
        r.capture(archive, "XLI", "c" * 40, lambda: pytest.fail("key forbidden"))


def test_wrapper_forbids_original_ref_writes_and_cross_asset_reads(monkeypatch):
    archive, _ = invented(monkeypatch)
    view = r.RecoveryArchive(archive, "XLI")
    with pytest.raises(ValueError, match="ORIGINAL_WRITE_FORBIDDEN"):
        view.write("/git/refs", {"ref": "refs/heads/" + r.m.ref("XLI", 0), "sha": "a" * 40})
    with pytest.raises(ValueError, match="REF_SCOPE"):
        view.ref(r.m.ref("XLB", 0))


def test_new_denial_retained_once_and_cannot_be_retried(monkeypatch):
    archive, _ = invented(monkeypatch)
    called = []

    def denied(product, _):
        called.append(product)
        return 403, b'{"message":"INVENTED_DENIAL"}'

    for _ in range(2):
        with pytest.raises(ValueError, match="HTTP_403_RETAINED_NO_RETRY"):
            r.capture(archive, "XLI", "c" * 40, lambda: "INVENTED", get=denied)
    assert len(called) == 1


def test_safe_failure_reports_archive_code_not_error_body(monkeypatch, capsys):
    monkeypatch.setattr(r.sys, "argv", ["recovery", "capture", "XLI", "b" * 40])
    monkeypatch.setattr(r, "guarded", lambda _: None)

    def fail(*_a, **_k):
        raise r.c.m.Stopped("GITHUB_PRIMARY_RATE_LIMIT")

    monkeypatch.setattr(r, "capture", fail)
    assert r.main() == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "BEAR2022_RECOVERY_GITHUB_PRIMARY_RATE_LIMIT"
    assert r.reason(ValueError("PRIVATE_PROVIDER_BODY")) == "BEAR2022_RECOVERY_UNVERIFIED"
