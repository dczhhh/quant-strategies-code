"""Independent bounded reference from published dates, not production helpers."""

from __future__ import annotations

from datetime import date, timedelta

from tools.bear_2022_sources import decode, need


def sessions(body):
    document = decode(body)
    need(
        document["schema"] == "bear_2022_independent_settlement_reference_v1"
        and document["settlement_cycle"] == "T+2"
        and document["historical_PIT_verified"] is False,
        "SETTLEMENT_REFERENCE_SCHEMA",
    )
    first = date.fromisoformat(document["first_session"])
    last = date.fromisoformat(document["last_reference_session"])
    need(first == date(2022, 1, 3) and last == date(2023, 1, 6), "SETTLEMENT_REFERENCE_WINDOW")
    closed = tuple(date.fromisoformat(day) for day in document["closed_weekdays"])
    need(
        tuple(sorted(set(closed))) == closed
        and all(first <= day <= last and day.weekday() < 5 for day in closed),
        "SETTLEMENT_REFERENCE_HOLIDAYS",
    )
    days, day = [], first
    while day <= last:
        if day.weekday() < 5 and day not in closed:
            days.append(day)
        day += timedelta(days=1)
    published = document["finra_published_trade_to_settlement_pairs"]
    boundary = document["derived_boundary_pairs_not_FINRA_2022_table_rows"]
    need(len(published) == 46 and len(boundary) == 2, "SETTLEMENT_REFERENCE_PAIR_COUNT")
    for trade, expected in (*published, *boundary):
        trade, expected = date.fromisoformat(trade), date.fromisoformat(expected)
        following = [day for day in days if day > trade]
        need(len(following) >= 2 and following[1] == expected, "SETTLEMENT_REFERENCE_PAIR_MISMATCH")
    return tuple(days)
