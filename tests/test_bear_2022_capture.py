"""Invented private archives only; no live provider requests or keys."""

import json

import pytest

from tests.test_bear_2022_sources import encoded, fixture, sessions
from tests.test_private_archive_migration import FakeGitHub
from tools import bear_2022_capture as a

SOURCE = "b" * 40


def response(product, _key):
    return 200, encoded(fixture(product["ticker"]))[product["id"]]


def test_capture_then_read_only_verification_does_not_request_or_write():
    archive = FakeGitHub(a.c.m.DESTINATION)
    first, made = a.capture(archive, "SPY", SOURCE, lambda: "INVENTED_KEY", get=response)
    assert len(first) == made == 4
    before = dict(archive.refs)
    archive.events.clear()
    second, made = a.capture(archive, "SPY", SOURCE, lambda: pytest.fail("key forbidden"),
                             verify_only=True, get=lambda *_: pytest.fail("network forbidden"))
    assert first == second and made == 0 and archive.refs == before
    assert all(method == "GET" for method, _ in archive.events)
    assert all(len(body) <= a.c.m.FILE_LIMIT for body in archive.blobs.values())


def test_unknown_outcome_is_preserved_and_cannot_be_retried():
    archive, called = FakeGitHub(a.c.m.DESTINATION), []

    def failed(product, key):
        called.append(product)
        if len(called) == 2:
            raise TimeoutError()
        return response(product, key)

    with pytest.raises(ValueError, match="UNKNOWN_OUTCOME_NO_RETRY"):
        a.capture(archive, "SPY", SOURCE, lambda: "INVENTED_KEY", get=failed)
    assert len(called) == 2 and archive.ref(a.ref("SPY", "raw")) is not None
    before = dict(archive.refs)
    with pytest.raises(ValueError, match="UNKNOWN_OUTCOME_NO_RETRY"):
        a.capture(archive, "SPY", SOURCE, lambda: pytest.fail("key forbidden"), get=failed)
    assert len(called) == 2 and archive.refs == before


@pytest.mark.parametrize("status", [301, 401, 403, 429, 500])
def test_non_200_is_retained_privately_and_stops_without_fallback(status):
    archive, called = FakeGitHub(a.c.m.DESTINATION), []

    def denied(product, _key):
        called.append(product)
        return status, b'{"message":"PRIVATE_DENIAL_BODY"}'

    with pytest.raises(ValueError, match="HTTP_" + str(status) + "_RETAINED_NO_RETRY"):
        a.capture(archive, "SPY", SOURCE, lambda: "INVENTED_KEY", get=denied)
    assert len(called) == 1 and archive.ref(a.ref("SPY", "raw")) is not None
    with pytest.raises(ValueError, match="RETAINED_NO_RETRY"):
        a.capture(archive, "SPY", SOURCE, lambda: pytest.fail("key forbidden"), get=denied)
    assert len(called) == 1


def test_missing_read_only_archive_cannot_create_a_reservation():
    archive = FakeGitHub(a.c.m.DESTINATION)
    with pytest.raises(ValueError, match="RAW_MISSING"):
        a.capture(archive, "SPY", SOURCE, lambda: pytest.fail("key forbidden"), verify_only=True)
    assert not archive.refs


def test_source_audit_is_rebuilt_and_every_byte_is_compared(monkeypatch):
    archive = FakeGitHub(a.c.m.DESTINATION)
    monkeypatch.setattr(a.c, "sessions", sessions)
    bodies = encoded(fixture())
    result, identity = a.audit(archive, "SPY", bodies, SOURCE, verify_only=False)
    assert result["economic_replay_ready"] is False and len(identity) == 64
    before = dict(archive.refs)
    assert a.audit(archive, "SPY", bodies, SOURCE, verify_only=True) == (result, identity)
    assert archive.refs == before
    with pytest.raises(ValueError, match="FRESH_AUDIT_BYTES"):
        a.audit(archive, "SPY", bodies, "c" * 40, verify_only=True)


def test_main_never_prints_provider_error_or_secret(monkeypatch, capsys):
    monkeypatch.setattr(a.sys, "argv", ["capture", "capture", "SPY", SOURCE])
    monkeypatch.setattr(a, "guarded", lambda _: None)
    monkeypatch.setattr(a.c, "PacedGitHub", lambda *_args, **_kw: FakeGitHub(a.c.m.DESTINATION))

    def failed(*_args, **_kw):
        raise ValueError("PRIVATE_PROVIDER_ERROR_AND_SECRET")

    monkeypatch.setattr(a, "capture", failed)
    assert a.main() == 1
    text = capsys.readouterr().out
    assert "PRIVATE_PROVIDER" not in text
    assert json.loads(text)["engine_invoked"] is False


def test_runner_guard_rejects_nonowner_before_network(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTOR", "untrusted")
    with pytest.raises(ValueError, match="RUNNER_CONTEXT"):
        a.guarded(SOURCE)


def test_all_12_assets_fit_unchanged_per_process_api_budget():
    for ticker in a.s.TICKERS:
        archive = FakeGitHub(a.c.m.DESTINATION)
        _, made = a.capture(archive, ticker, SOURCE, lambda: "INVENTED_KEY", get=response)
        assert made == 4
        assert len(archive.events) < a.c.m.CALL_LIMIT
