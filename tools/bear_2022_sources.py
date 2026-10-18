"""Frozen 2022 source qualification; no Engine, credentials, or network in this module."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

TICKERS = ("SPY", "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY")
WARMUP, FIRST, LAST = date(2020, 11, 2), date(2022, 1, 3), date(2022, 12, 30)
OVERLAP, CUTOFF = date(2021, 10, 19), date(2026, 10, 17)
NY = ZoneInfo("America/New_York")
PRICE_DIFFERENCE = Decimal("0.0002")
UNIT_SANITY_BPS = Decimal("100")
MAX_RESPONSE_BYTES = 524288
POLICY = {
    "schema": "bear_2022_source_qualification_v1",
    "universe": list(TICKERS),
    "warmup_start": str(WARMUP),
    "execution_start": str(FIRST),
    "execution_end": str(LAST),
    "overlap_start": str(OVERLAP),
    "split_metadata_cutoff": str(CUTOFF),
    "maximum_source_requests": 48,
    "requests_per_asset": 4,
    "maximum_response_bytes": MAX_RESPONSE_BYTES,
    "maximum_archive_parts_per_asset": 4,
    "minimum_presample_closes": 253,
    "overlap_difference_threshold_usd": str(PRICE_DIFFERENCE),
    "source_unit_sanity_ceiling_bps": str(UNIT_SANITY_BPS),
    "fit_price_rescaling_factor": False,
    "adjusted_close_used": False,
    "tail_prices_used": False,
    "automatic_retry_allowed": False,
    "redirects_allowed": False,
    "cookie_or_crumb_acquisition_allowed": False,
    "unknown_reservation_reset_allowed": False,
    "engine_invoked": False,
    "PIT_verified": False,
    "performance_admission": False,
}


def need(ok, code):
    if not ok:
        raise ValueError("BEAR2022_" + code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def digest(body):
    return hashlib.sha256(body).hexdigest()


def pairs(items):
    result = {}
    for key, value in items:
        need(key not in result, "DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def decode(body):
    need(type(body) is bytes and 0 < len(body) <= MAX_RESPONSE_BYTES, "BODY_SIZE")
    return json.loads(body, parse_float=Decimal, object_pairs_hook=pairs)


def number(value):
    need(type(value) in {int, Decimal, float, str}, "NUMBER_TYPE")
    value = Decimal(str(value))
    need(value.is_finite() and value > 0, "NUMBER_VALUE")
    return value


def epoch(day):
    return int(datetime.combine(day, datetime.min.time(), UTC).timestamp())


def products(ticker):
    need(ticker in TICKERS, "TICKER_SCOPE")
    common = {"ticker": ticker}
    yahoo = "https://query1.finance.yahoo.com/v8/finance/chart/" + ticker + "?"
    result = []
    for kind, first, last, interval, events in (
        ("yahoo_daily", WARMUP, LAST, "1d", "div,splits,capitalGains"),
        ("yahoo_tail_splits", date(2023, 1, 1), CUTOFF, "1mo", "splits"),
    ):
        result.append(
            {
                **common,
                "id": kind,
                "kind": kind,
                "start": str(first),
                "end": str(last),
                "url": yahoo
                + urlencode(
                    {
                        "period1": epoch(first),
                        "period2": epoch(last + timedelta(days=1)),
                        "interval": interval,
                        "events": events,
                        "includePrePost": "false",
                    }
                ),
            }
        )
    result.append(
        {
            **common,
            "id": "massive_daily",
            "kind": "daily",
            "start": str(OVERLAP),
            "end": str(LAST),
            "url": f"https://api.massive.com/v2/aggs/ticker/{ticker}/range/1/day/"
            f"{OVERLAP}/{LAST}?adjusted=false&sort=asc&limit=5000",
        }
    )
    result.append(
        {
            **common,
            "id": "massive_splits",
            "kind": "splits",
            "start": str(OVERLAP),
            "end": str(CUTOFF),
            "url": f"https://api.massive.com/stocks/v1/splits?ticker={ticker}"
            f"&execution_date.gte={OVERLAP}&execution_date.lte={CUTOFF}"
            "&sort=execution_date.asc&limit=100",
        }
    )
    return tuple(result)


PLAN_HASH = digest(canonical({"policy": POLICY, "products": {t: products(t) for t in TICKERS}}))


def yahoo(body, ticker, kind):
    root = decode(body)
    chart = root.get("chart", {})
    need(chart.get("error") is None, "YAHOO_RESPONSE_ERROR")
    rows = chart.get("result")
    need(type(rows) is list and len(rows) == 1, "YAHOO_RESULT_COUNT")
    row = rows[0]
    meta = row.get("meta", {})
    need(
        meta.get("symbol") == ticker
        and meta.get("currency") == "USD"
        and meta.get("exchangeTimezoneName") in {"America/New_York", "US/Eastern"},
        "YAHOO_IDENTITY",
    )
    events = row.get("events", {})
    need(
        type(events) is dict and not set(events) - {"splits", "dividends", "capitalGains"},
        "YAHOO_EVENT_KIND",
    )
    need(not events.get("capitalGains"), "YAHOO_CAPITAL_GAIN_EVENT_UNSUPPORTED")
    first, last = (WARMUP, LAST) if kind == "yahoo_daily" else (date(2023, 1, 1), CUTOFF)
    splits, dividends = {}, {}
    for event_kind, target in (("splits", splits), ("dividends", dividends)):
        for key, event in events.get(event_kind, {}).items():
            stamp = event.get("date")
            need(type(stamp) is int and str(stamp) == key, "YAHOO_EVENT_TIMESTAMP")
            day = datetime.fromtimestamp(stamp, UTC).astimezone(NY).date()
            need(first <= day <= last and day not in target, "YAHOO_EVENT_DATE")
            if event_kind == "splits":
                numerator, denominator = (
                    number(event.get("numerator")),
                    number(event.get("denominator")),
                )
                ratio = event.get("splitRatio")
                need(type(ratio) is str and ratio.count(":") == 1, "YAHOO_SPLIT_RATIO")
                left, right = ratio.split(":")
                need(
                    number(left) / number(right) == numerator / denominator, "YAHOO_SPLIT_CONFLICT"
                )
                target[day] = numerator / denominator
            else:
                target[day] = number(event.get("amount"))
    if kind == "yahoo_tail_splits":
        need(not dividends, "TAIL_DIVIDEND_OUTSIDE_PLAN")
        return {}, splits, {}
    times = row.get("timestamp")
    quotes = row.get("indicators", {}).get("quote")
    need(type(times) is list and type(quotes) is list and len(quotes) == 1, "YAHOO_QUOTE_SCHEMA")
    closes = quotes[0].get("close")
    need(type(closes) is list and len(times) == len(closes) > 0, "YAHOO_CLOSE_COUNT")
    result, previous = {}, -1
    for stamp, close in zip(times, closes, strict=True):
        need(type(stamp) is int and stamp > previous, "YAHOO_CLOSE_ORDER")
        previous = stamp
        day = datetime.fromtimestamp(stamp, UTC).astimezone(NY).date()
        need(first <= day <= last and day not in result, "YAHOO_CLOSE_DATE")
        result[day] = number(close)
    return result, splits, dividends


def massive(body, ticker, kind):
    value = decode(body)
    need(value.get("status") in {"OK", "DELAYED"} and not value.get("next_url"), "MASSIVE_RESPONSE")
    rows = value.get("results", [])
    need(type(rows) is list, "MASSIVE_RESULTS")
    if kind == "daily":
        need(value.get("ticker") == ticker and value.get("adjusted") is False, "MASSIVE_IDENTITY")
        need(value.get("resultsCount", len(rows)) == len(rows), "MASSIVE_COUNT")
    result, previous = {}, None
    for row in rows:
        if kind == "daily":
            stamp = row.get("t")
            need(type(stamp) is int, "MASSIVE_TIMESTAMP")
            at = datetime.fromtimestamp(stamp / 1000, UTC).astimezone(NY)
            need(at.hour == at.minute == at.second == at.microsecond == 0, "MASSIVE_DAILY_CLOCK")
            day, price = at.date(), number(row.get("c"))
            last = LAST
        else:
            need(row.get("ticker") == ticker, "MASSIVE_SPLIT_IDENTITY")
            day = date.fromisoformat(row["execution_date"])
            price = number(row.get("split_to")) / number(row.get("split_from"))
            last = CUTOFF
        need(
            OVERLAP <= day <= last and day not in result and (previous is None or day > previous),
            "MASSIVE_DATE_ORDER",
        )
        result[day], previous = price, day
    return result


def qualify(ticker, bodies, sessions):
    """Retain every difference, never fit prices, and never invoke a trading engine."""
    need(set(bodies) == {p["id"] for p in products(ticker)}, "SOURCE_SET")
    closes, splits, dividends = yahoo(bodies["yahoo_daily"], ticker, "yahoo_daily")
    _, tail, _ = yahoo(bodies["yahoo_tail_splits"], ticker, "yahoo_tail_splits")
    need(not set(splits) & set(tail), "SPLIT_SOURCE_OVERLAP")
    splits = dict(sorted((splits | tail).items()))
    reference = massive(bodies["massive_daily"], ticker, "daily")
    reference_splits = massive(bodies["massive_splits"], ticker, "splits")
    need(
        {d: r for d, r in splits.items() if d >= OVERLAP} == reference_splits,
        "SPLIT_SOURCE_CONFLICT",
    )
    expected = tuple(sessions(WARMUP, LAST))
    need(tuple(closes) == expected, "YAHOO_SESSION_COVERAGE")
    need(tuple(reference) == tuple(sessions(OVERLAP, LAST)), "MASSIVE_SESSION_COVERAGE")
    need(sum(day < FIRST for day in closes) >= 253, "PRESAMPLE_COVERAGE")
    need(all(day in closes for day in splits if day <= LAST), "SPLIT_NOT_TRADING_DAY")
    need(all(day in closes for day in dividends), "DIVIDEND_NOT_TRADING_DAY")

    def factor(day):
        value = Decimal(1)
        for effective, ratio in splits.items():
            if effective > day:
                value *= ratio
        return value

    original = {day: close * factor(day) for day, close in closes.items()}
    differences = [
        {
            "session": day,
            "yahoo_received_close": closes[day],
            "subsequent_split_factor": factor(day),
            "yahoo_derived_original_share_close": original[day],
            "massive_original_share_close": price,
            "difference_usd": original[day] - price,
            "absolute_difference_bps": abs(original[day] / price - 1) * 10000,
        }
        for day, price in reference.items()
    ]
    maximum = max(row["absolute_difference_bps"] for row in differences)
    need(maximum <= UNIT_SANITY_BPS, "SOURCE_UNIT_SANITY_FAILED")
    return {
        "schema": POLICY["schema"],
        "ticker": ticker,
        "plan_sha256": PLAN_HASH,
        "source_hashes": {key: digest(value) for key, value in sorted(bodies.items())},
        "policy": POLICY,
        "summary": {
            "status": "SOURCE_UNIT_AND_COVERAGE_CHECK_PASS_NOT_BACKTEST_ADMISSION",
            "daily_sessions": len(closes),
            "presample_sessions": sum(day < FIRST for day in closes),
            "overlap_sessions": len(reference),
            "overlap_differences_above_original_threshold": sum(
                abs(row["difference_usd"]) > PRICE_DIFFERENCE for row in differences
            ),
            "maximum_absolute_overlap_difference_bps": maximum,
            "split_events": len(splits),
            "subsequent_split_events": sum(day > LAST for day in splits),
            "dividend_events": len(dividends),
            "source_prices_exactly_equivalent": all(
                row["difference_usd"] == 0 for row in differences
            ),
            "same_year_2022_distribution_classification_verified": False,
            "settlement_reference_coverage_verified": False,
            "economic_replay_ready": False,
            "engine_invoked": False,
            "PIT_verified": False,
            "performance_admission": False,
        },
        "derivation_not_source_replacement": {
            "closes": [{"session": day, "close": price} for day, price in original.items()],
            "dividends": [
                {"ex_date": day, "original_share_cash": cash * factor(day)}
                for day, cash in sorted(dividends.items())
            ],
            "splits": [{"session": day, "new_per_old": ratio} for day, ratio in splits.items()],
            "overlap_differences": differences,
        },
    }
