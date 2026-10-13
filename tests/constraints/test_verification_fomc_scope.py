"""FOMC coverage follows studied exchange sessions, never unrelated event history."""

from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest
from test_raw_validation import freeze_fact
from test_research_pit import document
from test_research_pit_supply import common
from test_verification_orchestration import fixture, freeze, rejected, run

from quant_constraints.calendar import NY, SessionCalendar
from quant_constraints.research.data.pit_supply import (
    MACRO_TYPES,
    MacroCoverageRevision,
    MacroScheduleRevision,
)
from quant_constraints.research.data.verification_contracts import EvidenceRole

KNOWN = datetime(2025, 1, 1, tzinfo=NY)
CALENDAR = SessionCalendar()


def at(day, clock="00:00"):
    return datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=NY)


def research(day):
    """Move invented, internally consistent 5C/PIT data to one real exchange session."""
    req, records = fixture(macro=True)
    start = CALENDAR.session(date.fromisoformat(day)).market_open
    end = start + timedelta(minutes=1)
    split_day = CALENDAR.shift(start.astimezone(NY).date(), 1)
    anchor = CALENDAR.session(split_day).market_open + timedelta(minutes=1)
    req = replace(req, window_start=start, window_end=end, asof=end)
    for role in (EvidenceRole.RAW, EvidenceRole.COMPARISON):
        records[role] = tuple(
            replace(bar, bar_start_at=start, bar_end_at=end, available_at=end)
            for bar in records[role]
        )
    action = replace(
        records[EvidenceRole.ACTIONS][0],
        effective_date=split_day,
        historically_known_at=KNOWN,
    )
    records[EvidenceRole.ACTIONS] = (action,)
    records[EvidenceRole.FACTS] = (
        freeze_fact(
            replace(records[EvidenceRole.FACTS][0], trading_date=split_day, published_at=KNOWN)
        ),
    )
    records[EvidenceRole.DEFINITION] = (
        replace(
            records[EvidenceRole.DEFINITION][0],
            window_start=start,
            window_end=end,
            adjustment_anchor=anchor,
            action_coverage_start=start,
            action_coverage_end=anchor + timedelta(days=1),
        ),
    )
    records[EvidenceRole.PIT_EVENTS] = (
        document(replace(records[EvidenceRole.PIT_EVENTS][0], record=action, published_at=KNOWN)),
    )
    records[EvidenceRole.PIT_IDENTITIES] = tuple(
        document(replace(item, historically_known_at=KNOWN, published_at=KNOWN))
        for item in records[EvidenceRole.PIT_IDENTITIES]
    )
    records[EvidenceRole.PIT_COVERAGE] = (
        document(
            replace(
                records[EvidenceRole.PIT_COVERAGE][0],
                covered_from=date(2025, 1, 1),
                covered_until=date(2026, 1, 1),
                historically_known_at=KNOWN,
                published_at=KNOWN,
            )
        ),
    )
    records[EvidenceRole.MACRO_EVENTS] = ()
    records[EvidenceRole.MACRO_COVERAGE] = ()
    return req, records


def schedule(records, day, clock="14:00", *, kind="FOMC_STATEMENT", cancelled=False):
    return document(
        MacroScheduleRevision(
            **common(
                source=records[EvidenceRole.IDENTITIES][0].source,
                historically_known_at=KNOWN,
                published_at=KNOWN,
                cancelled=cancelled,
            ),
            event_type=kind,
            scheduled_at=at(day, clock),
        )
    )


def proof(records, start, end, name="coverage"):
    return document(
        MacroCoverageRevision(
            **common(
                source=records[EvidenceRole.IDENTITIES][0].source,
                historically_known_at=KNOWN,
                published_at=KNOWN,
                evidence_id=name,
            ),
            covered_from=start,
            covered_until=end,
            event_types=MACRO_TYPES,
            point_in_time=True,
            missing=False,
        )
    )


def complete_session(records, day, name="study"):
    session = CALENDAR.session(date.fromisoformat(day))
    return proof(records, session.market_open, session.market_close, name)


def consistent(req, records):
    frozen = freeze(req, records)
    report = run(frozen)
    assert report["status"] == "synthetic_contract_pass", report
    assert report["domain_validation_performed"] is True
    assert run(frozen) == report
    for flag in (
        "raw_verified",
        "PIT_verified",
        "archive_ready",
        "trusted_entry",
        "may_build_engine",
    ):
        assert report[flag] is False
    return report


@pytest.mark.parametrize("event_day", ["2025-09-17", "2025-10-22"])
@pytest.mark.parametrize("kind", ["FOMC_STATEMENT", "FOMC_PRESS"])
def test_unrelated_old_or_known_future_fomc_does_not_expand_october_window(event_day, kind):
    req, records = research("2025-10-17")
    event = schedule(records, event_day, kind=kind)
    records[EvidenceRole.MACRO_EVENTS] = (event,)
    records[EvidenceRole.MACRO_COVERAGE] = (
        complete_session(records, "2025-10-17"),
        proof(records, event.scheduled_at, event.scheduled_at + timedelta(hours=1), "unrelated"),
    )
    consistent(req, records)
    # Relevance does not bypass 5D's own evidence requirement for visible events.
    records[EvidenceRole.MACRO_COVERAGE] = records[EvidenceRole.MACRO_COVERAGE][:1]
    rejected(run(freeze(req, records)), "PIT_MACRO_EVENT_COVERAGE", "supply")


@pytest.mark.parametrize("kind", ["FOMC_STATEMENT", "FOMC_PRESS"])
@pytest.mark.parametrize("gap", ["missing", "microsecond"])
def test_related_fomc_requires_entire_previous_session(kind, gap):
    req, records = research("2025-10-17")
    records[EvidenceRole.MACRO_EVENTS] = (schedule(records, "2025-10-17", kind=kind),)
    current = complete_session(records, "2025-10-17")
    records[EvidenceRole.MACRO_COVERAGE] = (current,)
    previous = CALENDAR.session(date(2025, 10, 16))
    if gap == "microsecond":
        boundary = previous.market_open + timedelta(hours=1)
        records[EvidenceRole.MACRO_COVERAGE] = (
            proof(records, previous.market_open, boundary, "previous-a"),
            proof(
                records, boundary + timedelta(microseconds=1), current.covered_until, "previous-b"
            ),
        )
    rejected(run(freeze(req, records)), "PIT_COVERAGE_UNKNOWN", "supply")


@pytest.mark.parametrize(
    "study,event_day,event_clock,boundary",
    [
        ("2025-11-03", "2025-10-31", "14:00", "2025-11-01"),  # Month and autumn DST.
        ("2025-03-10", "2025-03-07", "14:00", "2025-03-09"),  # Spring DST.
        ("2025-12-01", "2025-11-28", "12:50", "2025-12-01"),  # Half-day then month.
        ("2025-12-01", "2025-11-28", "14:00", "2025-12-01"),  # Release after half-day close.
    ],
)
@pytest.mark.parametrize("gap", [False, True])
def test_next_session_fomc_requires_continuous_adjacent_boundary_proofs(
    study, event_day, event_clock, boundary, gap
):
    req, records = research(study)
    event = schedule(records, event_day, event_clock)
    records[EvidenceRole.MACRO_EVENTS] = (event,)
    opening = CALENDAR.session(date.fromisoformat(event_day)).market_open
    close = CALENDAR.session(date.fromisoformat(study)).market_close
    records[EvidenceRole.MACRO_COVERAGE] = (
        proof(records, opening, at(boundary) - timedelta(microseconds=int(gap)), "previous"),
        proof(records, at(boundary), close, "current"),
    )
    if gap:
        rejected(run(freeze(req, records)), "PIT_COVERAGE_UNKNOWN", "supply")
    else:
        consistent(req, records)


def test_after_halfday_close_does_not_create_a_second_following_session():
    req, records = research("2025-12-02")
    event = schedule(records, "2025-11-28", "14:00")
    records[EvidenceRole.MACRO_EVENTS] = (event,)
    records[EvidenceRole.MACRO_COVERAGE] = (
        complete_session(records, "2025-12-02"),
        proof(records, event.scheduled_at, event.scheduled_at + timedelta(hours=1), "unrelated"),
    )
    consistent(req, records)


def test_fomc_day_and_next_session_are_both_required_in_a_multisession_window():
    req, records = fixture(frequency="1d", macro=True)
    records[EvidenceRole.MACRO_EVENTS] = (
        document(
            replace(
                records[EvidenceRole.MACRO_EVENTS][0],
                event_type="FOMC_STATEMENT",
                scheduled_at=at("2025-11-14", "14:00"),
            )
        ),
    )
    consistent(req, records)
    records[EvidenceRole.MACRO_COVERAGE] = (
        document(
            replace(
                records[EvidenceRole.MACRO_COVERAGE][0],
                covered_from=at("2025-11-14"),
            )
        ),
    )
    rejected(run(freeze(req, records)), "PIT_COVERAGE_UNKNOWN", "supply")


def test_nontrading_or_cancelled_fomc_does_not_fabricate_relevant_session():
    req, records = research("2025-10-20")
    event = schedule(records, "2025-10-19")
    records[EvidenceRole.MACRO_EVENTS] = (event,)
    records[EvidenceRole.MACRO_COVERAGE] = (
        complete_session(records, "2025-10-20"),
        proof(records, event.scheduled_at, event.scheduled_at + timedelta(hours=1), "weekend"),
    )
    consistent(req, records)
    records[EvidenceRole.MACRO_EVENTS] = (schedule(records, "2025-10-20", cancelled=True),)
    records[EvidenceRole.MACRO_COVERAGE] = records[EvidenceRole.MACRO_COVERAGE][:1]
    consistent(req, records)
