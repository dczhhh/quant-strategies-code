"""Frozen minute/action request contracts with invented responses and archives."""

import pytest

from tests.test_private_archive_migration import FakeGitHub
from tools import bear_2022_market as m

SOURCE = "b" * 40
BODY = b'{"status":"OK","results":[]}'


def test_fixed_market_plan_fits_requested_2022_scope():
    assert sum(len(m.products(ticker)) for ticker in m.s.TICKERS) == 1032
    for ticker in m.s.TICKERS:
        rows = m.products(ticker)
        assert len(rows) == 86 and len(m.groups(ticker)) == 5
        assert all(len(group) <= 21 for group in m.groups(ticker))
        assert len({row["id"] for row in rows}) == 86
        assert {row["kind"] for row in rows} == {"dividends", "splits", "minute"}
        assert rows[0]["start"] == "2020-11-02" and rows[0]["end"] == "2022-12-30"
        assert rows[1]["end"] == "2021-10-18"
        assert rows[2]["start"] == "2022-01-03" and rows[-1]["end"] == "2022-12-30"
        assert all("adjusted=false" in row["url"] for row in rows[2:])
    assert not m.POLICY["engine_invoked"] and not m.POLICY["automatic_retry_allowed"]
    assert m.POLICY["new_daily_aggregate_requests"] == 0


def test_all_groups_fresh_read_without_network_or_writes_and_within_client_budget():
    archive = FakeGitHub(m.c.m.DESTINATION)
    first, made = m.capture(archive, "SPY", SOURCE, lambda: "INVENTED_KEY", get=lambda *_: (200, BODY))
    assert len(first) == made == 86
    assert len(archive.events) < m.c.m.CALL_LIMIT
    assert all(len(body) <= m.c.m.FILE_LIMIT for body in archive.blobs.values())
    before = dict(archive.refs)
    archive.events.clear()
    second, made = m.capture(
        archive,
        "SPY",
        SOURCE,
        lambda: pytest.fail("credential forbidden"),
        verify_only=True,
        get=lambda *_: pytest.fail("network forbidden"),
    )
    assert first == second and made == 0 and archive.refs == before
    assert all(method == "GET" for method, _ in archive.events)


@pytest.mark.parametrize("status", [301, 401, 403, 429, 500])
def test_non_200_stops_and_retains_original_without_retry(status):
    archive, calls = FakeGitHub(m.c.m.DESTINATION), []

    def denied(product, key):
        calls.append(product)
        return status, b"PRIVATE_FAILURE_BODY"

    for _ in range(2):
        with pytest.raises(ValueError, match="MARKET_HTTP_" + str(status) + "_RETAINED_NO_RETRY"):
            m.capture(archive, "SPY", SOURCE, lambda: "INVENTED_KEY", get=denied)
    assert len(calls) == 1 and archive.ref(m.ref("SPY", 0)) is not None
    assert archive.ref(m.ref("SPY", 1)) is None


def test_timeout_is_retained_and_unknown_reservation_cannot_be_reset():
    archive, calls = FakeGitHub(m.c.m.DESTINATION), []

    def unknown(product, key):
        calls.append(product)
        if len(calls) == 3:
            raise TimeoutError()
        return 200, BODY

    for _ in range(2):
        with pytest.raises(ValueError, match="MARKET_UNKNOWN_OUTCOME_NO_RETRY"):
            m.capture(archive, "SPY", SOURCE, lambda: "INVENTED_KEY", get=unknown)
    assert len(calls) == 3 and archive.ref(m.ref("SPY", 0)) is not None
    assert archive.ref(m.ref("SPY", 1)) is None


def test_missing_read_only_and_invalid_runner_fail_before_any_fetch(monkeypatch):
    archive = FakeGitHub(m.c.m.DESTINATION)
    with pytest.raises(ValueError, match="MARKET_RAW_MISSING"):
        m.capture(archive, "SPY", SOURCE, lambda: pytest.fail("key forbidden"), verify_only=True)
    assert not archive.refs
    monkeypatch.setenv("GITHUB_ACTOR", "untrusted")
    with pytest.raises(ValueError, match="MARKET_RUNNER_CONTEXT"):
        m.guarded(SOURCE)
