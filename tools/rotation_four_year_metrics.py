"""Calendar-year/quarter reporting for a single uninterrupted account path."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, localcontext

from tools import rotation_stress_analytics as a


def periods(daily, initial, *, quarterly=False):
    result, groups, before = [], {}, float(initial)
    for row in daily:
        day = row["date"]
        key = f"{day.year}Q{(day.month - 1) // 3 + 1}" if quarterly else str(day.year)
        groups.setdefault(key, []).append(row)
    for key, rows in groups.items():
        end = rows[-1]["NAV"]
        result.append(
            {
                "period": key,
                "first_session": str(rows[0]["date"]),
                "last_session": str(rows[-1]["date"]),
                "sessions": len(rows),
                "start_NAV_usd": before,
                "end_NAV_usd": end,
                "return_pct": (end / before - 1) * 100,
                "maximum_daily_drawdown_pct": a.drawdown([r["NAV"] for r in rows], before),
                "average_exposure_pct": sum(r["exposure"] for r in rows) / len(rows) * 100,
            }
        )
        before = end
    return result


def path_metrics(daily, initial):
    initial = float(initial)
    values = [row["NAV"] for row in daily]
    elapsed = (daily[-1]["date"] - daily[0]["date"]).days + 1
    with localcontext() as context:
        context.prec = 60
        returns = [Decimal(str(row["return"])) for row in daily]
        mean = sum(returns, Decimal(0)) / Decimal(len(returns))
        variance = (
            sum(((value - mean) ** 2 for value in returns), Decimal(0)) / Decimal(len(returns) - 1)
            if len(returns) > 1
            else Decimal(0)
        )
        sharpe = float(mean / variance.sqrt() * Decimal(252).sqrt()) if variance > 0 else None
    return {
        "return_pct": (values[-1] / initial - 1) * 100,
        "CAGR_pct": ((values[-1] / initial) ** (365.25 / elapsed) - 1) * 100,
        "CAGR_calendar_days_inclusive": elapsed,
        "maximum_daily_drawdown_pct": a.drawdown(values, initial),
        "annualized_daily_volatility_pct": a.volatility([row["return"] for row in daily]),
        "daily_Sharpe_zero_cash_rate": sharpe,
        "end_NAV_usd": values[-1],
        "end_cash_usd": daily[-1]["cash"],
        "end_settled_cash_usd": daily[-1]["settled_cash"],
        "end_receivables_usd": daily[-1]["net_receivables"],
        "average_exposure_pct": sum(row["exposure"] for row in daily) / len(daily) * 100,
        "average_cash_pct": sum(row["cash"] / row["NAV"] for row in daily) / len(daily) * 100,
        "years": periods(daily, initial),
        "quarters": periods(daily, initial, quarterly=True),
        "cash_interest_rate": 0,
        "Sharpe_cash_rate": 0,
    }


def monthly_net_trades(fills):
    rows = defaultdict(
        lambda: {
            "net_shares": Decimal(0),
            "buy_notional_usd": Decimal(0),
            "sell_notional_usd": Decimal(0),
            "commission_usd": Decimal(0),
            "fills": 0,
        }
    )
    for fill in fills:
        key = (fill["timestamp"].astimezone(a.NY).strftime("%Y-%m"), fill["asset"])
        row = rows[key]
        buy = a.side(fill) == "buy"
        quantity, price = a.D(fill["quantity"]), a.D(fill["price"])
        row["net_shares"] += quantity * (1 if buy else -1)
        row["buy_notional_usd" if buy else "sell_notional_usd"] += quantity * price
        row["commission_usd"] += a.D(fill["commission"])
        row["fills"] += 1
    return [
        {"month": month, "asset": asset, **value} for (month, asset), value in sorted(rows.items())
    ]


def factor_effects(cells):
    # Fixed 2x2 contrasts, expressed as percentage-point return differences.
    values = {name: cells[name]["return_pct"] for name in ("M", "M_T15R", "T15_S", "T15_SR")}
    return {
        "rotation_15_session_main_effect_pp": (
            (values["T15_S"] - values["M"]) + (values["T15_SR"] - values["M_T15R"])
        )
        / 2,
        "recovery_15_session_main_effect_pp": (
            (values["M_T15R"] - values["M"]) + (values["T15_SR"] - values["T15_S"])
        )
        / 2,
        "interaction_pp": (values["T15_SR"] - values["T15_S"]) - (values["M_T15R"] - values["M"]),
        "method": "fixed_arithmetic_return_contrasts_no_optimization",
    }
