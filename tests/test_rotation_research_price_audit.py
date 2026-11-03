"""Invented data only: private close diagnostics and counterfactual mark arithmetic."""

import copy
import json
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from tests.test_rotation_research_operator import normalized_sources as _source_fixture
from tools import rotation_research_replay as u
from tools.rotation_research_price_audit import ClosePriceAudit, fixed_holdings_marks, stats

c = u.c
normalized_sources = _source_fixture


def portfolio(inputs):
    trace = []
    for b in inputs.bars:
        if b.asset == "SPY" and b.end == c.CAL.session(b.end.date()).market_close:
            trace.append(
                {
                    "timestamp": b.end,
                    "cash": Decimal(750),
                    "receivables": Decimal(5),
                    "positions": {"SPY": 2},
                    "NAV": Decimal(755) + 2 * b.close,
                }
            )
    return {"engine": {"input_fingerprint": inputs.fingerprint}, "trace": trace}


def build(normalized_sources):
    docs, _, _ = normalized_sources
    audit = ClosePriceAudit()
    inputs, _ = u.build_inputs(lambda: None, "d" * 40, docs, audit=audit)
    fake = portfolio(inputs)
    return audit, inputs, {"rotation": fake, "funded_spy": copy.deepcopy(fake)}


def introduce_boundary_difference(normalized_sources):
    _, _, new = normalized_sources
    product = next(p for p in c.products("SPY") if p["kind"] == "minute")
    held = new["SPY"][product["id"]]
    body = json.loads(held["body"])
    stamp = int(c.CAL.session(c.FIRST).market_close.timestamp() * 1000) - 60000
    row = next(r for r in body["results"] if r["t"] == stamp)
    post = dict(row, t=stamp + 60000)
    row.update({key: row[key] + 0.02 for key in ("o", "h", "l", "c")})
    body["results"].append(post)
    body["results"].sort(key=lambda r: r["t"])
    body["resultsCount"] = len(body["results"])
    held["body"] = c.m.canonical(body)
    return product, held


def test_requested_empty_and_outside_request_are_not_conflated(normalized_sources):
    introduce_boundary_difference(normalized_sources)
    audit, inputs, portfolios = build(normalized_sources)
    report = audit.finish(inputs, portfolios, Decimal(1000))
    summary = report["summary"]
    assert summary["pairs"] == 576 and summary["mismatches"] == 1
    assert summary["per_asset"]["SPY"]["mismatches"] == 1
    assert summary["per_asset"]["XLB"]["mismatches"] == 0
    post = summary["near_close_coverage_by_start_offset_minutes"]["0"]
    assert post == {
        "outside_retained_request": 192,
        "requested_no_bar": 383,
        "retained_bar": 1,
        "within_daily_tolerance": 1,
    }
    last = summary["near_close_coverage_by_start_offset_minutes"]["-1"]
    assert last == {"retained_bar": 576, "within_daily_tolerance": 575}
    assert summary["economic_cause_verified"] is False
    assert summary["decision_sensitivity_verified"] is False
    assert summary["order_or_signal_oracle_verified"] is False
    assert summary["reconciled"] is False
    assert summary["official_close_trade_verified"] is False


def test_public_statistics_have_no_source_prices_or_per_date_rows(normalized_sources):
    audit, inputs, portfolios = build(normalized_sources)
    report = audit.finish(inputs, portfolios, 1000)

    def keys(value):
        if isinstance(value, dict):
            return set(value) | set().union(*(keys(v) for v in value.values()), set())
        if isinstance(value, list):
            return set().union(*(keys(v) for v in value), set())
        return set()

    assert not {
        "daily_close",
        "minute_close",
        "near_close_minutes",
        "per_session",
        "positions",
        "requests",
        "cash_and_net_receivables",
    } & keys(report["summary"])
    assert len(report["pairs"]) == 576
    assert len(report["per_session"]) == 48
    assert report["requests"] and all(r["sha256"] for r in report["requests"])


def test_fixed_holdings_mark_change_has_hand_computed_cash_receivable_and_no_retrade(
    normalized_sources,
):
    introduce_boundary_difference(normalized_sources)
    audit, inputs, portfolios = build(normalized_sources)
    original = copy.deepcopy(portfolios)
    report = audit.finish(inputs, portfolios, 1000)
    first = report["fixed_holdings_marks"]["rotation"][0]
    assert first["cash_and_net_receivables"] == Decimal(755)
    assert first["daily_minus_minute_NAV_usd"] == Decimal("-0.04")
    sensitivity = report["summary"]["fixed_holdings_mark_sensitivity"]["rotation"]
    assert Decimal(sensitivity["maximum_absolute_NAV_shift_bps_of_initial_cash"]) == Decimal("0.4")
    assert Decimal(sensitivity["daily_minus_minute_return_percentage_points"]) == 0
    assert sensitivity["decision_or_execution_sensitivity"] is False
    assert portfolios == original


def test_source_clock_handles_dst_and_retains_start_not_end_bucket(normalized_sources):
    audit, inputs, portfolios = build(normalized_sources)
    report = audit.finish(inputs, portfolios, 1000)
    for day, offset in (("2025-10-31", "-04:00"), ("2025-11-03", "-05:00")):
        pair = next(p for p in report["pairs"] if p["asset"] == "SPY" and p["session"] == day)
        last = next(
            x for x in pair["near_close_minutes"] if x["offset_from_session_close_minutes"] == -1
        )
        assert last["start"] == day + "T15:59:00" + offset
        assert last["end"] == day + "T16:00:00" + offset


def test_nearest_rank_percentiles_bins_and_original_tolerance():
    pairs = [
        {"relative_bps": Decimal(v), "difference_usd": Decimal(v)}
        for v in ("0", "0.1", "1", "5", "10", "11")
    ]
    result = stats(pairs)
    assert result["absolute_relative_bps_nearest_rank"] == {
        "p50": "1",
        "p90": "11",
        "p95": "11",
        "p99": "11",
        "max": "11",
    }
    assert sum(result["absolute_relative_bps_bins"].values()) == 6
    tiny = [
        {"relative_bps": Decimal(1), "difference_usd": Decimal(v)}
        for v in ("0.0002", "0.00020000000000001", "-0.0002")
    ]
    assert stats(tiny)["mismatches"] == 1


@pytest.mark.parametrize(
    "corruption", ["hash", "adjusted", "url", "duplicate", "outside", "pagination"]
)
def test_source_mutation_or_invalid_basis_fails_closed(normalized_sources, corruption):
    _, _, new = normalized_sources
    product = next(p for p in c.products("SPY") if p["kind"] == "minute")
    body = new["SPY"][product["id"]]["body"]
    source = u.source(product["id"], body, new["SPY"][product["id"]]["receipt"], product["url"])
    value = json.loads(body)
    if corruption == "hash":
        body += b" "
    elif corruption == "url":
        source = replace(source, url=source.url.replace("adjusted=false", "adjusted=true"))
    else:
        if corruption == "adjusted":
            value["adjusted"] = True
        elif corruption == "duplicate":
            value["results"].insert(1, value["results"][0])
        elif corruption == "outside":
            value["results"][0]["t"] -= 120000
        else:
            value["next_url"] = "https://example.invalid/next"
        body = c.m.canonical(value)
        source = replace(source, sha256=c.m.sha256(body))
    with pytest.raises(ValueError, match="PRICE_AUDIT_"):
        ClosePriceAudit().observe(product, source, body)


@pytest.mark.parametrize("corruption", ["missing", "duplicate", "reorder", "nav"])
def test_mark_sensitivity_requires_complete_accurate_daily_nav(normalized_sources, corruption):
    _, inputs, portfolios = build(normalized_sources)
    trace = portfolios["rotation"]["trace"]
    if corruption == "missing":
        trace.pop(0)
    elif corruption == "duplicate":
        trace.insert(0, trace[0])
    elif corruption == "reorder":
        trace.reverse()
    else:
        trace[0]["NAV"] += Decimal("0.0000001")
    with pytest.raises(ValueError, match="PRICE_AUDIT_NAV_"):
        fixed_holdings_marks(inputs, trace, 1000)


def test_early_close_window_uses_exchange_close_not_fixed_1600():
    day = datetime.fromisoformat("2025-11-28T12:59:00-05:00")
    lower = int((day - timedelta(minutes=1)).timestamp() * 1000)
    upper = int(day.timestamp() * 1000)
    product = {
        "ticker": "SPY",
        "url": f"https://api.massive.com/v2/aggs/ticker/SPY/range/1/minute/{lower}/{upper}?adjusted=false",
    }
    body = c.m.canonical(
        {"status": "OK", "ticker": "SPY", "adjusted": False, "results": [{"t": upper, "c": 100}]}
    )
    source = u.source(
        "early", body, {"response_received_at": "2026-10-16T00:00:00+00:00"}, product["url"]
    )
    audit = ClosePriceAudit()
    audit.observe(product, source, body)
    assert audit.minutes["early", day] == 100
