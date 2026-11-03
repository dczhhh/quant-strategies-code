"""Invented responses only: finite compressed capture and immutable no-retry reservations."""

import copy
import gzip
import json
from datetime import date
from pathlib import Path

import pytest
import yaml

from tests.test_private_archive_migration import FakeGitHub
from tools import rotation_stress_capture as a

c = a.c
SOURCE = "b" * 40


def response(product, _key):
    return 200, c.m.canonical(
        {"status": "OK", "ticker": product["ticker"], "adjusted": False, "results": []}
    )


def test_plan_matches_exact_inventory_dates_and_budget():
    assert sum(len(a.products(t)) for t in c.p.TICKERS) == 612
    assert len(a.groups("SPY")) == 3
    assert all(len(g) == 17 for g in a.groups("SPY"))
    assert 12 * 3 * ((1 + 3) + (a.MAX_PARTS + 1 + 3)) == 432
    rows = a.products("SPY")
    assert rows[0]["start"] == "2023-11-01" and rows[0]["end"] == "2024-08-23"
    assert rows[1]["end"] == rows[2]["end"] == "2024-08-25"
    minutes = rows[3:]
    days = [d for p in minutes for d in c.sessions(c.a.day(p["start"]), c.a.day(p["end"]))]
    assert len(days) == len(set(days)) == 143
    assert date(2025, 1, 9) not in days
    with pytest.raises(ValueError, match="SCOPE"):
        a.products("GME")


def test_append_only_packed_capture_then_fresh_read_needs_no_key():
    archive = FakeGitHub(c.m.DESTINATION)
    first, made = a.capture(
        archive, "SPY", SOURCE, lambda: "INVENTED_KEY", get=response, check_plan=False
    )
    assert len(first) == made == 51
    refs = copy.deepcopy(archive.refs)
    archive.events.clear()
    second, made = a.capture(
        c.r.CachedArchive(archive),
        "SPY",
        SOURCE,
        lambda: pytest.fail("key forbidden"),
        verify_only=True,
        get=lambda *_: pytest.fail("request forbidden"),
        check_plan=False,
    )
    assert first == second and made == 0 and archive.refs == refs
    assert all(method == "GET" for method, _ in archive.events)
    assert all(len(v) <= c.m.FILE_LIMIT for v in archive.blobs.values())


def test_unknown_group_is_not_retried_or_reset():
    archive = FakeGitHub(c.m.DESTINATION)
    prefix = a.PREFIX + "spy/0/"
    c.b.create(
        archive,
        a.reference("SPY", 0, True),
        {prefix + "reservation.json": a.reservation("SPY", 0, SOURCE)},
        c.m.BOOTSTRAP,
    )
    refs = dict(archive.refs)
    with pytest.raises(ValueError, match="UNKNOWN"):
        a.capture(
            archive,
            "SPY",
            SOURCE,
            lambda: pytest.fail("key forbidden"),
            get=lambda *_: pytest.fail("request forbidden"),
            check_plan=False,
        )
    assert archive.refs == refs


def test_partial_network_failure_preserves_unknown_group_without_retry():
    archive = FakeGitHub(c.m.DESTINATION)
    called = []

    def failed(p, key):
        called.append(p)
        if len(called) == 2:
            raise TimeoutError()
        return response(p, key)

    with pytest.raises(ValueError, match="UNKNOWN"):
        a.capture(archive, "SPY", SOURCE, lambda: "INVENTED_KEY", get=failed, check_plan=False)
    assert archive.ref(a.reference("SPY", 0)) is not None
    assert archive.ref(a.reference("SPY", 0, True)) is not None
    with pytest.raises(ValueError, match="UNKNOWN"):
        a.capture(
            archive,
            "SPY",
            SOURCE,
            lambda: pytest.fail("key forbidden"),
            get=failed,
            check_plan=False,
        )
    assert len(called) == 2


def test_bad_http_body_is_retained_privately_then_rejected_without_new_requests():
    archive = FakeGitHub(c.m.DESTINATION)
    calls = []

    def denied(p, _):
        calls.append(p)
        return 403, b'{"status":"NOT_AUTHORIZED","message":"invented private body"}'

    with pytest.raises(ValueError, match="HTTP_REJECTED"):
        a.capture(archive, "SPY", SOURCE, lambda: "INVENTED_KEY", get=denied, check_plan=False)
    assert len(calls) == 1
    assert archive.ref(a.reference("SPY", 0)) is not None
    with pytest.raises(ValueError, match="HTTP_REJECTED"):
        a.capture(
            archive,
            "SPY",
            SOURCE,
            lambda: pytest.fail("key forbidden"),
            get=denied,
            check_plan=False,
        )
    assert len(calls) == 1


def test_expansion_limit_and_duplicate_json_reject():
    with pytest.raises(ValueError, match="EXPANSION"):
        a.bounded_gunzip(gzip.compress(b"x" * 1000), 100)
    with pytest.raises(c.m.Stopped):
        c.b.decode(b'{"key":1,"key":2}')


def test_fixed_inventory_is_checked_before_credentials_or_any_write(monkeypatch):
    archive = FakeGitHub(c.m.DESTINATION)

    def deny(_):
        raise ValueError("invented inventory conflict")

    monkeypatch.setattr(a, "check_inventory", deny)
    with pytest.raises(ValueError, match="inventory conflict"):
        a.capture(archive, "SPY", SOURCE, lambda: pytest.fail("key forbidden"), get=response)
    assert not archive.refs and all(method == "GET" for method, _ in archive.events)


def test_main_failure_never_prints_response_or_credential(monkeypatch, capsys):
    monkeypatch.setattr(a.sys, "argv", ["capture", "capture", "SPY", SOURCE])
    monkeypatch.setattr(c, "guarded", lambda _: None)
    monkeypatch.setattr(c, "PacedGitHub", lambda *_args, **_kwargs: FakeGitHub(c.m.DESTINATION))

    def fail(*_args, **_kwargs):
        raise ValueError("invented private raw or secret")

    monkeypatch.setattr(a, "capture", fail)
    assert a.main() == 1
    output = capsys.readouterr().out
    assert "invented private" not in output
    assert json.loads(output)["engine_invoked"] is False


def test_inventory_contract_and_manifest_are_bound_before_capture(monkeypatch):
    archive = FakeGitHub(c.m.DESTINATION)
    report = {
        "contract": a.s.CONTRACT,
        "summary": {"new_vendor_requests": 0},
        "proposed_products_not_authorized_by_this_inventory": [
            p for t in c.p.TICKERS for p in a.products(t)
        ],
    }

    def install(value, source=a.INVENTORY_SOURCE):
        body = gzip.compress(a.u.canonical(value), mtime=0)
        payload = {a.s.PREFIX + "inventory.json.gz": body}
        payload[a.s.PREFIX + "manifest.json"] = a.u.canonical(
            {
                "source_commit": source,
                "contract_sha256": a.s.CONTRACT_HASH,
                "files": {a.s.PREFIX + "inventory.json.gz": c.m.sha256(body)},
            }
        )
        target = FakeGitHub(c.m.DESTINATION)
        c.b.create(target, a.s.REPORT_REF, payload, c.m.BOOTSTRAP)
        monkeypatch.setattr(
            a,
            "INVENTORY_HASH",
            c.m.sha256(a.u.canonical({k: c.m.sha256(v) for k, v in sorted(payload.items())})),
        )
        return target

    archive = install(report)
    a.check_inventory(archive)
    changed = copy.deepcopy(report)
    changed["proposed_products_not_authorized_by_this_inventory"].pop()
    with pytest.raises(ValueError, match="PLAN_CONFLICT"):
        a.check_inventory(install(changed))
    with pytest.raises(ValueError, match="MANIFEST_CONFLICT"):
        a.check_inventory(install(report, "d" * 40))


def test_full_capture_and_fresh_verification_fit_finite_private_api_budget():
    archive = FakeGitHub(c.m.DESTINATION)
    for ticker in c.p.TICKERS:
        _, made = a.capture(
            c.r.CachedArchive(archive),
            ticker,
            SOURCE,
            lambda: "INVENTED_KEY",
            get=response,
            check_plan=False,
        )
        assert made == 51
    before = dict(archive.refs)
    for _ in range(3):  # Independent capture verify plus two report input reconstructions.
        for ticker in c.p.TICKERS:
            _, made = a.capture(
                c.r.CachedArchive(archive),
                ticker,
                SOURCE,
                lambda: pytest.fail("key forbidden"),
                verify_only=True,
                check_plan=False,
            )
            assert made == 0
    assert archive.refs == before
    # Leave explicit headroom for G1, original sources, issuer docs and report writes.
    assert sum(method == "POST" for method, _ in archive.events) <= 432
    assert len(archive.events) + 1200 < 5000


def test_workflow_has_finite_scope_and_no_provider_key_in_preflight_or_verify():
    doc = yaml.safe_load(Path("docs/examples/rotation-stress-capture.yml").read_text())
    assert doc.get("on", doc.get(True)) == {
        "push": {"branches": ["main"], "paths": [c.OPERATION_WORKFLOW]}
    }
    assert "MASSIVE" not in json.dumps(doc["jobs"]["preflight"]) + json.dumps(doc["jobs"]["verify"])
    assert "ARCHIVE_WRITE_TOKEN" not in json.dumps(doc["jobs"]["preflight"])
    assert doc["jobs"]["capture"]["strategy"]["max-parallel"] == 2
    assert doc["jobs"]["capture"]["strategy"]["matrix"]["ticker"] == list(c.p.TICKERS)
    assert doc["jobs"]["verify"]["needs"] == "capture"
    assert "MASSIVE_API_KEY" in json.dumps(doc["jobs"]["capture"])
