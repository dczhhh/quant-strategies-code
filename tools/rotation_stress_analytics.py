"""Source-bound descriptive attribution; no price substitution, optimization or alpha admission."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, time
from decimal import Decimal, localcontext

from quant_constraints.calendar import NY, SessionCalendar

CAL = SessionCalendar()


def D(value):
    return Decimal(str(value))


def need(condition, code):
    if not condition:
        raise ValueError("STRESS_ANALYTICS_" + code)


def side(fill):
    return getattr(fill["side"], "value", fill["side"])


def drawdown(nav, initial):
    peak, worst = initial, 0.0
    for value in nav:
        peak, worst = max(peak, value), min(worst, value / max(peak, value) - 1)
    return worst * 100


def volatility(returns):
    if len(returns) < 2:
        return None
    values = [D(v) for v in returns]
    average = sum(values) / len(values)
    variance = sum((v - average) ** 2 for v in values) / (len(values) - 1)
    return float((variance * 252).sqrt() * 100)


def net_accruals(inputs, assumptions, fills):
    quantities, net = defaultdict(Decimal), defaultdict(Decimal)
    first = inputs.bars[0].end.astimezone(NY).date()
    events = [
        (datetime.combine(e.session, time(), NY), 0, e) for e in inputs.splits if e.session >= first
    ]
    events += [
        (datetime.combine(e.ex_date, time(), NY), 1, e)
        for e in inputs.distributions
        if e.ex_date >= first
    ]
    events += [(f["timestamp"], 2, f) for f in fills]
    events.sort(key=lambda e: (e[0], e[1]))
    for _, kind, event in events:
        if kind == 0:
            quantities[event.asset] *= D(event.new_per_old)
        elif kind == 1:
            net[event.ex_date, event.asset] += (
                quantities[event.asset]
                * D(event.cash_amount)
                * (1 - D(assumptions.dividend_tax_rate))
            )
        else:
            quantities[event["asset"]] += D(event["quantity"]) * (1 if side(event) == "buy" else -1)
    return net


def portfolio(inputs, assumptions, result):
    """Every asset's marked value plus trade flows/net accrual minus fees sums to NAV PnL."""
    with localcontext() as context:
        context.prec = 60
        daily = [
            r
            for r in result["trace"]
            if r["timestamp"] == CAL.session(r["timestamp"].astimezone(NY).date()).market_close
        ]
        need(bool(daily), "DAILY_TRACE_MISSING")
        bars = {(b.end, b.asset): b for b in inputs.bars}
        fills = defaultdict(list)
        for fill in result["fills"]:
            fills[fill["timestamp"].astimezone(NY).date()].append(fill)
        accrual = net_accruals(inputs, assumptions, result["fills"])
        assets = sorted(s.asset for s in inputs.securities)
        previous = dict.fromkeys(assets, Decimal(0))
        prior_nav = D(assumptions.initial_cash)
        month_nav = prior_nav
        all_contributions = defaultdict(Decimal)
        months, records, within, month_contributions, month_fills, month_returns = (
            [],
            [],
            [],
            defaultdict(Decimal),
            [],
            [],
        )
        for index, state in enumerate(daily):
            stamp, nav = state["timestamp"], D(state["NAV"])
            day = stamp.astimezone(NY).date()
            values = {a: D(state["positions"].get(a, 0)) * D(bars[stamp, a].close) for a in assets}
            contributions = {a: values[a] - previous[a] + accrual[day, a] for a in assets}
            for fill in fills[day]:
                signed = D(fill["quantity"]) * D(fill["price"]) * (1 if side(fill) == "buy" else -1)
                contributions[fill["asset"]] -= signed + D(fill["commission"])
            need(
                abs(sum(contributions.values()) - (nav - prior_nav)) <= Decimal("1E-8"),
                "ASSET_PNL_NAV_BRIDGE",
            )
            for asset in assets:
                month_contributions[asset] += contributions[asset]
                all_contributions[asset] += contributions[asset]
            month_fills += fills[day]
            weights = {a: float(v / nav) for a, v in values.items() if v != 0}
            records.append(
                {
                    "date": day,
                    "NAV": float(nav),
                    "cash": state["cash"],
                    "settled_cash": state["settled_cash"],
                    "net_receivables": state["receivables"],
                    "exposure": sum(weights.values()),
                    "weights": weights,
                    "return": float(nav / prior_nav - 1),
                }
            )
            within.append(float(nav))
            month_returns.append(records[-1]["return"])
            is_end = index == len(daily) - 1 or daily[index + 1]["timestamp"].astimezone(
                NY
            ).strftime("%Y-%m") != day.strftime("%Y-%m")
            if is_end:
                months.append(
                    {
                        "month": day.strftime("%Y-%m"),
                        "return_pct": float((nav / month_nav - 1) * 100),
                        "maximum_daily_drawdown_pct": drawdown(within, float(month_nav)),
                        "annualized_daily_volatility_pct": volatility(month_returns),
                        "fills": len(month_fills),
                        "commission_usd": sum(float(f["commission"]) for f in month_fills),
                        "gross_notional_turnover_over_start_NAV": float(
                            sum(D(f["quantity"]) * D(f["price"]) for f in month_fills) / month_nav
                        ),
                        "cash_usd": float(state["cash"]),
                        "settled_cash_usd": float(state["settled_cash"]),
                        "receivables_usd": float(state["receivables"]),
                        "end_holdings_weights": weights,
                        "asset_net_pnl_usd": {
                            a: float(v) for a, v in month_contributions.items() if v != 0
                        },
                    }
                )
                month_nav, within, month_contributions, month_fills, month_returns = (
                    nav,
                    [],
                    defaultdict(Decimal),
                    [],
                    [],
                )
            previous, prior_nav = values, nav
        need(
            abs(sum(all_contributions.values()) - (prior_nav - D(assumptions.initial_cash)))
            <= Decimal("1E-8"),
            "TOTAL_ATTRIBUTION_BRIDGE",
        )
        return {
            "daily": records,
            "months": months,
            "asset_net_pnl_usd": {
                a: float(v)
                for a, v in sorted(all_contributions.items(), key=lambda p: (-p[1], p[0]))
                if v != 0
            },
            "annualized_daily_volatility_pct": volatility([r["return"] for r in records]),
            "end_NAV_usd": float(prior_nav),
            "end_cash_usd": float(records[-1]["cash"]),
            "end_settled_cash_usd": float(records[-1]["settled_cash"]),
            "end_receivables_usd": float(records[-1]["net_receivables"]),
            "end_holdings_weights": records[-1]["weights"],
            "attribution_bridge_tolerance_usd": "1E-8",
        }


def regression(strategy_returns, market_returns):
    with localcontext() as context:
        context.prec = 60
        return _regression(strategy_returns, market_returns)


def _regression(strategy_returns, market_returns):
    y, x = [D(v) for v in strategy_returns], [D(v) for v in market_returns]
    need(len(y) == len(x) and all(v.is_finite() for v in (*x, *y)), "REGRESSION_INPUTS")
    result = {
        "status": "INCONCLUSIVE",
        "observations": len(x),
        "method": "OLS_intercept_CAPM_zero_cash_rate_Newey_West_HAC_fixed_5_lags",
        "confidence_level": 0.95,
        "minimum_observations_for_stability_gate": 252,
        "out_of_sample_verified": False,
        "performance_admission": False,
    }
    if len(x) < 10:
        return result | {"reason": "INSUFFICIENT_OR_CONSTANT_MARKET_SAMPLE"}
    n, sx, sy, sxx = D(len(x)), sum(x), sum(y), sum(v * v for v in x)
    centered_x = sxx - sx * sx / n
    if centered_x / n <= D("1E-20"):
        return result | {"reason": "INSUFFICIENT_OR_CONSTANT_MARKET_SAMPLE"}
    beta = (sum(a * b for a, b in zip(x, y, strict=True)) - sx * sy / n) / centered_x
    alpha = (sy - beta * sx) / n
    residual = [a - alpha - beta * b for a, b in zip(y, x, strict=True)]
    # Bartlett Newey-West sandwich, fixed five lags, no small-sample correction.
    # Decimal arithmetic avoids host/BLAS-dependent report-byte differences.
    meat00 = sum(e * e for e in residual)
    meat01 = sum(e * e * value for e, value in zip(residual, x, strict=True))
    meat11 = sum(e * e * value * value for e, value in zip(residual, x, strict=True))
    for lag in range(1, 6):
        weight = D(1) - D(lag) / 6
        for t in range(lag, len(x)):
            term = weight * residual[t] * residual[t - lag]
            meat00 += 2 * term
            meat01 += term * (x[t] + x[t - lag])
            meat11 += 2 * term * x[t] * x[t - lag]
    determinant = n * sxx - sx * sx
    inv00, inv01, inv11 = sxx / determinant, -sx / determinant, n / determinant
    var_alpha = inv00**2 * meat00 + 2 * inv00 * inv01 * meat01 + inv01**2 * meat11
    var_beta = inv01**2 * meat00 + 2 * inv01 * inv11 * meat01 + inv11**2 * meat11
    need(var_alpha >= D("-1E-50") and var_beta >= D("-1E-50"), "INVALID_HAC_VARIANCE")
    critical = D("1.959963984540054")
    alpha_se, beta_se = max(Decimal(0), var_alpha).sqrt(), max(Decimal(0), var_beta).sqrt()
    total = sum((v - sy / n) ** 2 for v in y)
    return result | {
        "reason": "SHORT_SELECTED_SAMPLE_NOT_INDEPENDENT_OUT_OF_SAMPLE_VALIDATION",
        "daily_alpha_pct": float(alpha * 100),
        "linear_annualized_alpha_pct": float(alpha * 252 * 100),
        "daily_alpha_95pct_CI_pct": [
            float((alpha + sign * critical * alpha_se) * 100) for sign in (-1, 1)
        ],
        "beta": float(beta),
        "beta_95pct_CI": [float(beta + sign * critical * beta_se) for sign in (-1, 1)],
        "r_squared": float(1 - sum(e * e for e in residual) / total) if total else None,
        "numeric_policy": "Decimal60_HAC5_normal95CI_no_small_sample_correction",
    }


def describe(inputs, assumptions, rotation, benchmark, oracle):
    strategy, funded = (
        portfolio(inputs, assumptions, rotation),
        portfolio(inputs, assumptions, benchmark),
    )
    daily = strategy["daily"]
    need([r["date"] for r in daily] == [r["date"] for r in funded["daily"]], "BENCHMARK_CALENDAR")
    prices = {
        (b.end.astimezone(NY).date(), b.asset): b.close
        for b in inputs.bars
        if b.end == CAL.session(b.end.astimezone(NY).date()).market_close
    }
    div, split = defaultdict(Decimal), defaultdict(lambda: Decimal(1))
    for e in inputs.distributions:
        if e.asset == "SPY":
            div[e.ex_date] += D(e.cash_amount) * (1 - D(assumptions.dividend_tax_rate))
    for e in inputs.splits:
        if e.asset == "SPY":
            split[e.session] *= D(e.new_per_old)
    spy, returns, attribution, spy_index, matched_index = [], [], [], [1.0], [1.0]
    for prior, now in zip(daily, daily[1:]):
        day, old = now["date"], prior["date"]
        market_return = float(
            split[day] * (D(prices[day, "SPY"]) + div[day]) / D(prices[old, "SPY"]) - 1
        )
        spy.append(market_return)
        returns.append(now["return"])
        matched = prior["exposure"] * market_return
        attribution.append(
            {
                "date": day,
                "lagged_actual_A2_exposure": prior["exposure"],
                "SPY_net_unit_return": market_return,
                "attribution_return": matched,
            }
        )
        spy_index.append(spy_index[-1] * (1 + market_return))
        matched_index.append(matched_index[-1] * (1 + matched))
    peak_to_date, running = [], 1.0
    for value in spy_index:
        running = max(running, value)
        peak_to_date.append(running)
    trough = min(range(len(spy_index)), key=lambda i: spy_index[i] / peak_to_date[i] - 1)
    peak = max(range(trough + 1), key=lambda i: spy_index[i])
    phase_boundaries = (
        ("pre_peak", 0, peak),
        ("maximum_drawdown", peak, trough),
        ("post_trough_rebound", trough, len(daily) - 1),
    )
    phases = []
    for name, first, last in phase_boundaries:
        if first == last:
            continue
        phases.append(
            {
                "phase": name,
                "start_close": str(daily[first]["date"]),
                "end_close": str(daily[last]["date"]),
                "sessions": last - first,
                "A2_return_pct": (daily[last]["NAV"] / daily[first]["NAV"] - 1) * 100,
                "SPY_net_unit_return_pct": (spy_index[last] / spy_index[first] - 1) * 100,
                "funded_SPY_return_pct": (
                    funded["daily"][last]["NAV"] / funded["daily"][first]["NAV"] - 1
                )
                * 100,
            }
        )

    def capture(positive):
        indices = [i for i, value in enumerate(spy) if (value > 0 if positive else value < 0)]
        market = math.prod(1 + spy[i] for i in indices) - 1
        model = math.prod(1 + returns[i] for i in indices) - 1
        return {
            "sessions": len(indices),
            "compounded_subset_capture_pct": model / market * 100 if market else None,
        }

    risks = {}
    for row in oracle["signals"]:
        risks[row["signal_session"]] = row["regime"]
    changes, previous = [], None
    for day, regime in sorted(risks.items()):
        if regime != previous:
            changes.append({"signal_session": str(day), "regime": regime})
        previous = regime
    peak_day, trough_day = daily[peak]["date"], daily[trough]["date"]
    downgrade = next(
        (day for day, regime in sorted(risks.items()) if day >= peak_day and regime != "normal"),
        None,
    )
    recovery = next(
        (day for day, regime in sorted(risks.items()) if day > trough_day and regime == "normal"),
        None,
    )
    delays = {
        "basis": "ex_post_SPY_net_unit_peak_trough_not_strategy_inputs",
        "peak_close": str(peak_day),
        "trough_close": str(trough_day),
        "first_non_normal_signal_on_or_after_peak": str(downgrade) if downgrade else None,
        "trading_sessions_peak_to_non_normal_signal": CAL.distance(peak_day, downgrade)
        if downgrade
        else None,
        "first_normal_signal_after_trough": str(recovery) if recovery else None,
        "trading_sessions_trough_to_normal_signal": CAL.distance(trough_day, recovery)
        if recovery
        else None,
    }
    attribution_months = []
    for month in sorted({row["date"].strftime("%Y-%m") for row in attribution}):
        values = [
            r["attribution_return"] for r in attribution if r["date"].strftime("%Y-%m") == month
        ]
        path, value = [], 1.0
        for daily_return in values:
            value *= 1 + daily_return
            path.append(value)
        attribution_months.append(
            {
                "month": month,
                "close_intervals": len(values),
                "return_pct": (value - 1) * 100,
                "maximum_daily_drawdown_pct": drawdown(path, 1),
                "annualized_daily_volatility_pct": volatility(values),
            }
        )
    return {
        "schema": "rotation_stress_descriptive_analytics_v1",
        "rotation": strategy,
        "funded_SPY": funded,
        "exposure_matched_attribution": {
            "policy": "previous_close_actual_A2_exposure_times_SPY_net_unit_return_cash_zero_rate",
            "purpose": "EX_POST_ATTRIBUTION_NOT_AN_EXECUTABLE_SETTLEMENT_OR_COST_MATCHED_STRATEGY",
            "common_close_window_start": str(daily[0]["date"]),
            "end": str(daily[-1]["date"]),
            "first_partial_trading_day_excluded": True,
            "return_pct": (matched_index[-1] - 1) * 100,
            "maximum_daily_drawdown_pct": drawdown(matched_index, 1),
            "annualized_daily_volatility_pct": volatility(
                [r["attribution_return"] for r in attribution]
            ),
            "A2_common_close_return_pct": (daily[-1]["NAV"] / daily[0]["NAV"] - 1) * 100,
            "funded_SPY_common_close_return_pct": (
                funded["daily"][-1]["NAV"] / funded["daily"][0]["NAV"] - 1
            )
            * 100,
            "monthly": attribution_months,
            "daily": attribution,
        },
        "regression": regression(returns, spy),
        "up_capture": capture(True),
        "down_capture": capture(False),
        "ex_post_SPY_peak_trough_phases": phases,
        "phase_boundaries_were_not_strategy_inputs": True,
        "risk_signal_transitions": changes,
        "risk_signal_delays": delays,
        "whole_period_returns_include_first_partial_day_but_CAPM_uses_common_close_intervals": True,
        "cash_interest_rate": "0",
        "PIT_verified": False,
        "performance_admission": False,
    }
