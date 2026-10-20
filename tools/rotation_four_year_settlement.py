"""Independent explicit historical settlement dates, without production calendars."""

from __future__ import annotations

import json
from bisect import bisect_right
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from tools.rotation_four_year_inventory import FIRST, LAST, c, need

PATH = "config/research/rotation_four_year_settlement.json"


def reference(body=None):
    body = body if body is not None else (Path(__file__).resolve().parents[1] / PATH).read_bytes()
    value = json.loads(body)
    need(
        value["schema"] == "rotation_four_year_independent_settlement_reference_v1"
        and value["first"] == str(FIRST)
        and value["last"] == "2026-02-06"
        and value["cycle_by_trade_date"]
        == [
            {"first": "2022-01-03", "last": "2024-05-24", "days": 2},
            {"first": "2024-05-28", "last": "2025-12-31", "days": 1},
        ],
        "SETTLEMENT_REFERENCE_SCOPE",
    )
    closed = tuple(date.fromisoformat(d) for d in value["closed_weekdays"])
    need(closed == tuple(sorted(set(closed))), "SETTLEMENT_REFERENCE_DUPLICATE")
    first, last = date.fromisoformat(value["first"]), date.fromisoformat(value["last"])
    need(all(first <= d <= last and d.weekday() < 5 for d in closed), "SETTLEMENT_CLOSED_DAY")
    days, day = [], first
    while day <= last:
        if day.weekday() < 5 and day not in closed:
            days.append(day)
        day += timedelta(days=1)
    for row in value["explicit_open_exceptions"]:
        need(date.fromisoformat(row["date"]) in days, "SETTLEMENT_OPEN_EXCEPTION")
    return value, tuple(days), body


def due(trade, days):
    need(FIRST <= trade <= LAST, "SETTLEMENT_TRADE_SCOPE")
    delay = 2 if trade < date(2024, 5, 28) else 1
    index = bisect_right(days, trade) + delay - 1
    need(index < len(days), "SETTLEMENT_HORIZON")
    return days[index]


def audit(body=None):
    # Compare two execution collaborators against separately transcribed dates.
    # Neither production helper constructs the independent reference.
    from ml4t.backtest.accounting.settlement import us_equity_settlement_date
    from quant_constraints.calendar import settlement_date

    value, days, body = reference(body)
    pairs = []
    for trade in c.sessions(FIRST, LAST):
        expected = due(trade, days)
        need(
            settlement_date(trade) == us_equity_settlement_date(trade) == expected,
            "SETTLEMENT_PRODUCTION_REFERENCE_CONFLICT",
        )
        pairs.append({"trade": str(trade), "settlement": str(expected)})
    for row in value["published_boundary_pairs"]:
        need(
            due(date.fromisoformat(row["trade"]), days) == date.fromisoformat(row["settlement"]),
            "SETTLEMENT_PUBLISHED_PAIR",
        )
    for trade, expected in value["derived_boundary_pairs"]:
        need(
            due(date.fromisoformat(trade), days) == date.fromisoformat(expected),
            "SETTLEMENT_DERIVED_PAIR",
        )
    return {
        "schema": "rotation_four_year_settlement_audit_v1",
        "status": "TRANSCRIBED_PRIMARY_REFERENCE_AND_PRODUCTION_CALENDARS_MATCH",
        "reference_sha256": c.m.sha256(body),
        "trade_dates_verified": len(pairs),
        "settlement_sessions": dict(sorted(Counter(d.year for d in days).items())),
        "all_trade_to_settlement_pairs": pairs,
        "reference_kind": value["reference_kind"],
        "source_access_limitations": value["source_access_limitations"],
        "original_DTC_documents_newly_archived": False,
        "PIT_verified": False,
        "performance_admission": False,
    }
