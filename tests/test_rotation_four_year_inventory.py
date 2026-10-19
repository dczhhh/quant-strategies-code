"""Invented inventory rows only; no prices, issuer amounts or private fixtures."""

import copy
import json
from datetime import date

import pytest

from tests.test_private_archive_migration import FakeGitHub
from tools import rotation_four_year_inventory as f


def invented(day=date(2025, 11, 6)):
    sources, closes, bars = [], [], []
    session = f.c.CAL.session(day)
    for ticker in f.s.TICKERS:
        sid = ticker + "-invented"
        sources.append(
            {"identifier": sid, "url": "https://example.test/invented", "sha256": "a" * 64}
        )
        closes.append({"asset": ticker, "session": str(day), "close": "123.45", "source_id": sid})
        for stamp in f.q.w.f.x.s.required_minutes(day):
            bars.append(
                {
                    "asset": ticker,
                    "end": str(stamp),
                    "source_id": sid,
                    "open": "123",
                    "high": "124",
                    "low": "122",
                    "close": "123.45",
                    "volume": "100",
                }
            )
        product = next(p for p in f.s.products(ticker) if p["id"] == "massive_splits")
        sources.append(
            {"identifier": ticker + "-splits", "url": product["url"], "sha256": "b" * 64}
        )
    sources.append(
        {"identifier": "calendar", "url": "https://example.test/calendar", "sha256": "c" * 64}
    )
    value = {
        "sources": sources,
        "closes": closes,
        "bars": bars,
        "distributions": [],
        "settlement_sessions": [str(day), str(f.c.CAL.shift(day, 1))],
        "settlement_source_id": "calendar",
    }
    assert session is not None
    return {w: copy.deepcopy(value) for w in f.INPUT_PINS}, {t: {"events": []} for t in f.s.TICKERS}


def test_frozen_scope_and_no_real_admission():
    assert date(2022, 1, 3) == f.FIRST and date(2025, 12, 31) == f.LAST
    assert len(f.c.sessions(f.FIRST, f.LAST)) == 1003
    assert {y: len(f.c.sessions(date(y, 1, 1), date(y, 12, 31))) for y in range(2022, 2026)} == {
        2022: 251,
        2023: 250,
        2024: 252,
        2025: 250,
    }
    assert date(2025, 1, 9) not in f.c.sessions(f.FIRST, f.LAST)
    assert f.POLICY["engine_invoked"] is f.POLICY["continuous_inputs_ready"] is False
    assert f.POLICY["acquisition_executor_present"] is False
    assert f.POLICY["entire_private_repository_absence_certified"] is False
    assert len(f.registered_products()) > 1500


def test_one_missing_asset_is_not_full_universe_coverage():
    inputs, splits = invented()
    first, last = date(2025, 11, 6), date(2025, 11, 7)
    result = f.analyze(inputs, splits, first=first, last=last)
    assert result["summary"]["accepted_complete_execution_sessions"] == 1
    assert result["summary"]["missing_execution_sessions"] == 1
    for value in inputs.values():
        value["bars"] = [b for b in value["bars"] if b["asset"] != "XLY"]
    result = f.analyze(inputs, splits, first=first, last=last)
    assert result["summary"]["accepted_complete_execution_sessions"] == 0
    assert result["per_asset"]["SPY"]["complete_execution_sessions"] == 1


def test_request_scope_does_not_replace_missing_actual_rows_or_authorize_retry():
    inputs, splits = invented()
    day = date(2025, 11, 6)
    spec = f.product("SPY", "minute", day, day)
    for value in inputs.values():
        value["sources"].append(
            {"identifier": "requested-minute", "url": spec["url"], "sha256": "f" * 64}
        )
        value["bars"] = [b for b in value["bars"] if b["asset"] != "SPY"]
    result = f.analyze(inputs, splits, first=day, last=day)
    assert result["summary"]["any_requested_but_unrepresented_rows"] is True
    assert result["summary"]["accepted_complete_execution_sessions"] == 0
    assert result["per_asset"]["SPY"]["minute_requested_but_unrepresented_spans"]
    assert not any(
        p["ticker"] == "SPY" and p["kind"] == "minute"
        for p in result["proposed_products_not_executed"]
    )


def test_partial_session_and_frozen_registered_plan_are_not_automatic_retry():
    inputs, splits = invented(date(2025, 11, 5))
    day = date(2025, 11, 5)
    for value in inputs.values():
        value["bars"].pop()
    result = f.analyze(inputs, splits, first=day, last=day)
    assert result["per_asset"]["XLY"]["partial_execution_sessions"] == [str(day)]
    assert result["per_asset"]["XLY"]["minute_withheld_registered_plan_spans"]
    assert not any(p["kind"] == "minute" for p in result["proposed_products_not_executed"])


@pytest.mark.parametrize("kind", ["closes", "bars"])
def test_economic_overlap_conflict_and_duplicate_rows_stop(kind):
    inputs, splits = invented()
    inputs["primary"][kind][0]["close"] = "987.65"
    with pytest.raises(ValueError, match="OVERLAP_ECONOMIC_CONFLICT"):
        f.analyze(inputs, splits, first=date(2025, 11, 6), last=date(2025, 11, 6))
    inputs, splits = invented()
    inputs["primary"][kind].append(inputs["primary"][kind][0])
    with pytest.raises(ValueError, match="DUPLICATE_ROW"):
        f.analyze(inputs, splits, first=date(2025, 11, 6), last=date(2025, 11, 6))


def test_equivalent_decimal_amounts_do_not_conflict_or_upgrade_classification():
    inputs, splits = invented()
    for w, value in inputs.items():
        value["distributions"] = [
            {
                "asset": "SPY",
                "event_id": "invented",
                "ex_date": "2025-11-06",
                "record_date": "2025-11-06",
                "pay_date": "2026-01-02",
                "cash_amount": "1.0" if w == "primary" else "1.00",
                "classification": "unclassified_signal_only",
            }
        ]
    result = f.analyze(inputs, splits, first=date(2025, 11, 6), last=date(2025, 11, 6))
    assert result["summary"]["continuous_inputs_ready"] is False
    assert result["accepted_windows"]["primary"]["classifications"] == {
        "2025:unclassified_signal_only": 1
    }
    inputs["secondary"]["distributions"][0]["cash_amount"] = "2"
    with pytest.raises(ValueError, match="EVENT_OVERLAP_CONFLICT"):
        f.analyze(inputs, splits, first=date(2025, 11, 6), last=date(2025, 11, 6))


def test_minute_products_dst_half_day_no_covered_day_bridge():
    day = date(2025, 11, 28)
    row = f.product("SPY", "minute", day, day)
    end = int(row["url"].split("?")[0].split("/")[-1])
    assert end == int(f.c.CAL.session(day).market_close.timestamp() * 1000) - 1
    assert f.requested_product({"url": row["url"]})["first"] == day
    required = f.c.sessions(date(2025, 11, 26), date(2025, 12, 1))
    assert f.spans({required[0], required[-1]}, required) == [
        {"start": str(required[0]), "end": str(required[0]), "sessions": 1},
        {"start": str(required[-1]), "end": str(required[-1]), "sessions": 1},
    ]


@pytest.mark.parametrize(
    "alter",
    [
        lambda x: x.replace("adjusted=false", "adjusted=true"),
        lambda x: x + "&apiKey=INVENTED",
        lambda x: x.replace("range/1/day", "range/5/day"),
        lambda x: x + "&limit=5000",
    ],
)
def test_vendor_url_closed_allowlist(alter):
    url = f.product("SPY", "daily", date(2025, 11, 6), date(2025, 11, 7))["url"]
    with pytest.raises(ValueError):
        f.requested_product({"url": alter(url)})


@pytest.mark.parametrize("window", f.INPUT_PINS)
def test_pinned_normalized_input_readback_and_corruption(window, monkeypatch):
    archive = FakeGitHub(f.c.m.DESTINATION)
    raw = b'{"invented":true}'
    files = f.q.pack(dict.fromkeys(f.q.LOGICAL, raw), f.SOURCE, window)
    revision = f.c.b.create(archive, f.q.REPORT_REF + window, files, f.c.m.BOOTSTRAP)
    monkeypatch.setitem(f.INPUT_PINS, window, (revision, f.s.digest(raw)))
    assert f.load_inputs(archive, window) == {"invented": True}
    monkeypatch.setitem(f.INPUT_PINS, window, (revision, "f" * 64))
    with pytest.raises(ValueError, match="INPUT_LOGICAL_HASH"):
        f.load_inputs(archive, window)
    monkeypatch.setitem(f.INPUT_PINS, window, ("f" * 40, f.s.digest(raw)))
    with pytest.raises(ValueError, match="INPUT_REF_PIN"):
        f.load_inputs(archive, window)


def test_split_bodies_must_match_accepted_sources_and_ref(monkeypatch):
    sources = []
    bodies = {}
    for ticker in f.s.TICKERS:
        body = f.u.canonical(
            {
                "status": "OK",
                "results": [
                    {
                        "ticker": ticker,
                        "execution_date": "2025-12-05",
                        "split_from": 1,
                        "split_to": 2,
                    }
                ],
            }
        )
        bodies[ticker] = {"massive_splits": body}
        spec = next(p for p in f.s.products(ticker) if p["id"] == "massive_splits")
        sources.append(
            {
                "identifier": ticker + ":source:massive_splits",
                "sha256": f.s.digest(body),
                "url": spec["url"],
            }
        )
    archive = type(
        "Archive",
        (),
        {
            "ref": lambda _self, ref: next(
                pin for t, pin in f.SPLIT_PINS.items() if t.lower() + "-raw" in ref
            )
        },
    )()
    monkeypatch.setattr(f.a, "read_raw", lambda _archive, ticker: bodies[ticker])
    assert f.read_splits(archive, {"sources": sources})["SPY"]["events"] == [
        {"session": "2025-12-05", "new_per_old": "2"}
    ]
    sources[0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SPLIT_RAW_SOURCE_HASH"):
        f.read_splits(archive, {"sources": sources})


def test_report_create_only_fresh_read_only_verify():
    archive = FakeGitHub(f.c.m.DESTINATION)
    report = {"invented": True}
    files = f.payload(report, "a" * 40)
    with pytest.raises(ValueError, match="REPORT_MISSING"):
        f.retain(archive, files, verify_only=True)
    digest = f.retain(archive, files, verify_only=False)
    archive.events.clear()
    assert f.retain(archive, files, verify_only=True) == digest
    assert all(method == "GET" for method, _ in archive.events)
    files[f.PREFIX + "inventory.json.gz"] += b"bad"
    with pytest.raises(ValueError, match="REPORT_BYTES"):
        f.retain(archive, files, verify_only=False)
    assert "123.45" not in json.dumps(f.POLICY)
