"""Private source-price diagnostics; never repair prices or certify their cause."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from urllib.parse import parse_qs, urlsplit

from ml4t.backtest.strategies.sector_rotation.research_inputs import (
    RAW_CLOSE_TOLERANCE,
    number,
)
from quant_constraints.calendar import NY, SessionCalendar

CAL = SessionCalendar()
OFFSETS = tuple(range(-5, 6))


def require(condition, code):
    if not condition:
        raise ValueError(code)


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "PRICE_AUDIT_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def stats(pairs):
    relative = sorted(abs(p["relative_bps"]) for p in pairs)
    bins = Counter()
    for value in relative:
        label = next(
            (
                label
                for bound, label in (
                    (0, "zero"),
                    (Decimal("0.1"), "up_to_0_1bps"),
                    (1, "up_to_1bps"),
                    (5, "up_to_5bps"),
                    (10, "up_to_10bps"),
                )
                if value <= bound
            ),
            "above_10bps",
        )
        bins[label] += 1

    def percentile(percent):
        # Nearest-rank quantiles, not interpolation or source-price rounding.
        return (
            str(relative[max(0, (len(relative) * percent + 99) // 100 - 1)]) if relative else None
        )

    return {
        "pairs": len(pairs),
        "mismatches": sum(abs(p["difference_usd"]) > RAW_CLOSE_TOLERANCE for p in pairs),
        "minute_above_daily": sum(p["difference_usd"] > 0 for p in pairs),
        "minute_below_daily": sum(p["difference_usd"] < 0 for p in pairs),
        "absolute_relative_bps_nearest_rank": {
            "p50": percentile(50),
            "p90": percentile(90),
            "p95": percentile(95),
            "p99": percentile(99),
            "max": str(relative[-1]) if relative else None,
        },
        "absolute_relative_bps_bins": dict(sorted(bins.items())),
    }


class ClosePriceAudit:
    def __init__(self):
        self.requests = {}
        self.minutes = {}

    def observe(self, product, source, body):
        """Bind all retained near-close minutes, including available post-close rows."""
        require(source.identifier not in self.requests, "PRICE_AUDIT_DUPLICATE_SOURCE")
        require(hashlib.sha256(body).hexdigest() == source.sha256, "PRICE_AUDIT_SOURCE_HASH")
        url = urlsplit(source.url)
        parts = url.path.split("/")
        require(
            source.url == product["url"]
            and url.hostname == "api.massive.com"
            and parts[1:8] == ["v2", "aggs", "ticker", product["ticker"], "range", "1", "minute"]
            and len(parts) == 10
            and parse_qs(url.query).get("adjusted") == ["false"],
            "PRICE_AUDIT_REQUEST_BASIS",
        )
        lower, upper = int(parts[8]), int(parts[9])
        require(lower <= upper, "PRICE_AUDIT_REQUEST_RANGE")
        data = json.loads(
            body,
            parse_float=Decimal,
            object_pairs_hook=unique_pairs,
            parse_constant=lambda _: require(False, "PRICE_AUDIT_NONFINITE"),
        )
        require(
            data.get("ticker") == product["ticker"] and data.get("adjusted") is False,
            "PRICE_AUDIT_RESPONSE_IDENTITY",
        )
        require(
            data.get("status") in {"OK", "DELAYED"} and not data.get("next_url"),
            "PRICE_AUDIT_RESPONSE_INCOMPLETE",
        )
        self.requests[source.identifier] = {
            "source_id": source.identifier,
            "sha256": source.sha256,
            "url": source.url,
            "observed_at": source.observed_at.isoformat(),
            "from_ms": lower,
            "to_ms": upper,
            "ticker": product["ticker"],
            "adjusted": False,
        }
        previous = -1
        for row in data.get("results", []):
            stamp = row.get("t")
            require(
                type(stamp) is int
                and lower <= stamp <= upper
                and stamp > previous
                and stamp % 60000 == 0,
                "PRICE_AUDIT_MINUTE_CLOCK",
            )
            previous = stamp
            start = datetime.fromtimestamp(stamp // 1000, UTC).astimezone(NY)
            session = CAL.session(start.date())
            if session is None:
                continue
            offset = int((start - session.market_close).total_seconds() // 60)
            if offset in OFFSETS:
                close = number(row.get("c"))
                require(close > 0, "PRICE_AUDIT_NONPOSITIVE_CLOSE")
                self.minutes[source.identifier, start] = close

    def finish(self, inputs, portfolios, initial_cash):
        with localcontext() as context:
            context.prec = 60
            return self._finish(inputs, portfolios, number(initial_cash))

    def _finish(self, inputs, portfolios, initial_cash):
        require(initial_cash > 0, "PRICE_AUDIT_INITIAL_CASH")
        daily = {(r.asset, r.session): r for r in inputs.closes}
        pairs, by_asset, by_session = [], defaultdict(list), defaultdict(list)
        coverage = defaultdict(Counter)
        source_map = inputs.source_map
        for bar in inputs.bars:
            session = CAL.session(bar.end.astimezone(NY).date())
            if session is None or bar.end != session.market_close:
                continue
            day = bar.end.astimezone(NY).date()
            row = daily[bar.asset, day]
            source_id = bar.source_id
            require(
                source_id in self.requests
                and self.requests[source_id]["sha256"] == source_map[source_id].sha256,
                "PRICE_AUDIT_UNBOUND_MINUTE_SOURCE",
            )
            request = self.requests[source_id]
            require(request["ticker"] == bar.asset, "PRICE_AUDIT_SOURCE_TICKER")
            window = []
            for offset in OFFSETS:
                start = session.market_close.astimezone(NY) + timedelta(minutes=offset)
                stamp = int(start.timestamp()) * 1000
                price = self.minutes.get((source_id, start))
                requested = request["from_ms"] <= stamp <= request["to_ms"]
                status = (
                    "outside_retained_request"
                    if not requested
                    else "retained_bar"
                    if price is not None
                    else "requested_no_bar"
                )
                require(price is None or requested, "PRICE_AUDIT_OUTSIDE_REQUEST_BAR")
                difference = price - row.close if price is not None else None
                matches = difference is not None and abs(difference) <= RAW_CLOSE_TOLERANCE
                coverage[str(offset)][status] += 1
                if matches:
                    coverage[str(offset)]["within_daily_tolerance"] += 1
                window.append(
                    {
                        "start": start.isoformat(),
                        "start_ms": stamp,
                        "end": (start + timedelta(minutes=1)).isoformat(),
                        "offset_from_session_close_minutes": offset,
                        "coverage": status,
                        "close": price,
                        "difference_from_daily_usd": difference,
                        "within_daily_tolerance": matches,
                    }
                )
                if offset == -1:
                    require(price == bar.close, "PRICE_AUDIT_NORMALIZED_MINUTE_CONFLICT")
            difference = bar.close - row.close
            pair = {
                "asset": bar.asset,
                "session": str(day),
                "daily_source_id": row.source_id,
                "minute_source_id": source_id,
                "daily_close": row.close,
                "minute_close": bar.close,
                "difference_usd": difference,
                "relative_bps": difference / row.close * 10000,
                "near_close_minutes": window,
            }
            pairs.append(pair)
            by_asset[bar.asset].append(pair)
            by_session[str(day)].append(pair)
        aggregate = stats(pairs)
        require(
            aggregate["pairs"] == inputs.close_comparison["pairs"]
            and aggregate["mismatches"] == inputs.close_comparison["mismatches"],
            "PRICE_AUDIT_PAIR_COVERAGE",
        )
        private_marks, safe_marks = {}, {}
        for name, portfolio in sorted(portfolios.items()):
            require(
                portfolio["engine"]["input_fingerprint"] == inputs.fingerprint,
                "PRICE_AUDIT_PORTFOLIO_INPUT",
            )
            detail, summary = fixed_holdings_marks(inputs, portfolio["trace"], initial_cash)
            private_marks[name], safe_marks[name] = detail, summary
        summary = {
            "status": "SOURCE_PRICE_DIAGNOSTIC_COMPLETE_CAUSE_UNVERIFIED",
            **aggregate,
            "per_asset": {k: stats(v) for k, v in sorted(by_asset.items())},
            "near_close_coverage_by_start_offset_minutes": {
                k: dict(sorted(v.items()))
                for k, v in sorted(coverage.items(), key=lambda x: int(x[0]))
            },
            "fixed_holdings_mark_sensitivity": safe_marks,
            "reconciled": aggregate["mismatches"] == 0,
            "economic_cause_verified": False,
            "official_close_trade_verified": False,
            "decision_sensitivity_verified": False,
            "order_or_signal_oracle_verified": False,
            "PIT_verified": False,
        }
        return {
            "schema": "rotation_source_price_audit_v1",
            "input_fingerprint": inputs.fingerprint,
            "tolerance_usd": str(RAW_CLOSE_TOLERANCE),
            "summary": summary,
            "requests": [self.requests[k] for k in sorted(self.requests)],
            "pairs": pairs,
            "per_session": {k: stats(v) for k, v in sorted(by_session.items())},
            "fixed_holdings_marks": private_marks,
        }


def fixed_holdings_marks(inputs, trace, initial_cash):
    """Counterfactual valuation only: preserve orders, fills, cash and receivables."""
    initial_cash = number(initial_cash)
    daily = {(r.session, r.asset): r.close for r in inputs.closes}
    minute = {(b.end, b.asset): b.close for b in inputs.bars}
    expected = sorted(
        {
            b.end
            for b in inputs.bars
            if b.end == CAL.session(b.end.astimezone(NY).date()).market_close
        }
    )
    states = [s for s in trace if s["timestamp"] in set(expected)]
    require([s["timestamp"] for s in states] == expected, "PRICE_AUDIT_NAV_COVERAGE")
    details, minute_peak, daily_peak = [], initial_cash, initial_cash
    minute_dd = daily_dd = maximum_shift = Decimal(0)
    for state in states:
        stamp = state["timestamp"]
        base = number(state["cash"]) + number(state["receivables"])
        actual, alternate = base, base
        for asset, quantity in state["positions"].items():
            quantity = number(quantity)
            require(quantity >= 0, "PRICE_AUDIT_SHORT_POSITION")
            actual += quantity * minute[stamp, asset]
            alternate += quantity * daily[stamp.astimezone(NY).date(), asset]
        require(abs(actual - number(state["NAV"])) <= Decimal("1E-8"), "PRICE_AUDIT_NAV_RECOMPUTE")
        shift = alternate - actual
        maximum_shift = max(maximum_shift, abs(shift))
        minute_peak, daily_peak = max(minute_peak, actual), max(daily_peak, alternate)
        minute_dd, daily_dd = (
            min(minute_dd, actual / minute_peak - 1),
            min(daily_dd, alternate / daily_peak - 1),
        )
        details.append(
            {
                "timestamp": stamp.isoformat(),
                "cash_and_net_receivables": base,
                "minute_mark_NAV": actual,
                "daily_mark_NAV": alternate,
                "daily_minus_minute_NAV_usd": shift,
            }
        )
    require(bool(details), "PRICE_AUDIT_EMPTY_NAV")
    last = details[-1]
    return details, {
        "method": "fixed_executed_positions_cash_and_net_receivables_no_retrade",
        "minute_mark_return_pct": str((last["minute_mark_NAV"] / initial_cash - 1) * 100),
        "daily_mark_return_pct": str((last["daily_mark_NAV"] / initial_cash - 1) * 100),
        "daily_minus_minute_return_percentage_points": str(
            last["daily_minus_minute_NAV_usd"] / initial_cash * 100
        ),
        "maximum_absolute_NAV_shift_bps_of_initial_cash": str(maximum_shift / initial_cash * 10000),
        "minute_mark_maximum_daily_drawdown_pct": str(minute_dd * 100),
        "daily_mark_maximum_daily_drawdown_pct": str(daily_dd * 100),
        "decision_or_execution_sensitivity": False,
    }
