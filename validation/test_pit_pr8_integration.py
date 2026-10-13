"""Cross-branch compatibility with immutable PR #8; run in its isolated import process.

PIT_PR8_SOURCE points to a detached checkout at PR8_SHA, never a merged feature branch.
The checkout supplies gates/ledger; only the research namespace uses the current PR.
"""

import hashlib
import importlib
import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import polars as pl
import pytest

PR8_SHA = "b5ecfd8527fe8eb6398a09b7f74e802e4324789c"
ROOT = Path(__file__).resolve().parents[1]
PR8 = Path(os.environ["PIT_PR8_SOURCE"]).resolve()
snapshot = PR8 / "source-snapshot.json"
if snapshot.is_file():
    content = snapshot.read_bytes()
    if (
        hashlib.sha256(content).hexdigest()
        != "e5d23cf891c1955be4525acdc451451f400e654e9eee0361b9962cc780984110"
    ):
        raise ValueError("PR #8 snapshot manifest changed")
    record = json.loads(content)
    if record["source_revision"] != PR8_SHA or len(record["files"]) != 18:
        raise ValueError("PR #8 snapshot scope changed")
    expected = set()
    for item in record["files"]:
        path = item["path"]
        expected.add(path)
        body = (PR8 / path).read_bytes()
        identity = hashlib.sha1(
            b"blob " + str(len(body)).encode() + b"\0" + body, usedforsecurity=False
        ).hexdigest()
        if identity != item["git_blob_sha"]:
            raise ValueError("PR #8 snapshot source bytes changed")
    actual_paths = {str(path.relative_to(PR8)) for path in (PR8 / "src").rglob("*.py")}
    if actual_paths != expected:
        raise ValueError("PR #8 snapshot source files changed")
else:
    actual = subprocess.check_output(
        ["git", "-C", str(PR8), "rev-parse", "HEAD"], text=True
    ).strip()
    if actual != PR8_SHA:
        raise ValueError("PR #8 integration requires the pinned reviewed source SHA")
sys.path.insert(0, str(PR8 / "src"))
sys.path.insert(0, str(ROOT / "tests" / "constraints"))

# Runtime plugin imports are intentional: sibling interfaces are absent on this branch.
quant_constraints = importlib.import_module("quant_constraints")
if Path(quant_constraints.__file__).resolve() != PR8 / "src/quant_constraints/__init__.py":
    raise ValueError("Integration loaded an unexpected constraints implementation")
quant_constraints.__path__.append(str(ROOT / "src" / "quant_constraints"))
adapter_fixture = importlib.import_module("test_adapter")
corporate_fixture = importlib.import_module("test_corporate_actions")
supply_fixture = importlib.import_module("test_research_pit_supply")
pit_fixture = importlib.import_module("test_research_pit")
at, tick = adapter_fixture.at, adapter_fixture.tick
action, build, held = corporate_fixture.action, corporate_fixture.build, corporate_fixture.held
macro, proof, qualification, revise = (
    supply_fixture.macro,
    supply_fixture.proof,
    supply_fixture.qualification,
    supply_fixture.revise,
)
rule, supply, tax, tax_source = (
    supply_fixture.rule,
    supply_fixture.supply,
    supply_fixture.tax,
    supply_fixture.tax_source,
)
Action, ConstraintConfig = quant_constraints.Action, quant_constraints.ConstraintConfig
(
    DividendTaxQualification,
    DividendTaxRule,
    MacroEvent,
    MacroEventCoverage,
    MacroEventGate,
    resolve_dividend_tax_rate,
) = (
    getattr(quant_constraints, name)
    for name in (
        "DividendTaxQualification",
        "DividendTaxRule",
        "MacroEvent",
        "MacroEventCoverage",
        "MacroEventGate",
        "resolve_dividend_tax_rate",
    )
)
Intent = quant_constraints.Intent
MarketContext, State = quant_constraints.MarketContext, quant_constraints.State
SessionCalendar = importlib.import_module("quant_constraints.calendar").SessionCalendar
CorporateActionProcessor = importlib.import_module(
    "quant_constraints.corporate_actions"
).CorporateActionProcessor


def gate(p, **settings):
    adapter = p.macro_adapter(event_factory=MacroEvent, coverage_factory=MacroEventCoverage)
    return MacroEventGate(
        replace(ConstraintConfig(), macro_events_enabled=True, **settings),
        SessionCalendar(),
        adapter,
    )


def decision(g, clock="10:00", day="2026-10-12", quantity=20):
    return g.check(
        Intent("A", quantity),
        State(10000, 10000),
        MarketContext(at(day, clock), 100, instrument="plain_sector_etf", sector="tech"),
    )


@pytest.mark.parametrize(
    "kind,end", [("CPI", "10:30"), ("PPI", "10:15"), ("NFP", "10:30"), ("PCE", "10:30")]
)
def test_existing_gate_receives_0830_release_schedule(kind, end):
    g = gate(supply(macro_events=(macro(event_type=kind),)))
    assert decision(g).action is Action.DEFER
    assert decision(g, clock=end).action is Action.ALLOW
    assert decision(g, quantity=-20).action is Action.ALLOW


def test_dual_fomc_existing_gate_and_revision_cancel():
    first = macro(event_type="FOMC_STATEMENT", scheduled_at=at("2026-10-12", "14:00"))
    press = macro(
        evidence_id="press", event_type="FOMC_PRESS", scheduled_at=at("2026-10-12", "14:30")
    )
    g = gate(supply(macro_events=(first, press)))
    assert decision(g, "14:15").action is Action.DEFER
    changed = revise(first, "2026-10-13", scheduled_at=at("2026-10-14", "14:00"))
    cancelled = revise(changed, "2026-10-14", cancelled=True)
    g = gate(supply(macro_events=(first, changed, cancelled)))
    assert decision(g, "14:15").action is Action.DEFER
    assert decision(g, "14:15", "2026-10-13").action is Action.ALLOW
    assert decision(g, "14:15", "2026-10-14").action is Action.ALLOW


def test_missing_coverage_rejects_buys_preserves_risk_sell():
    g = gate(supply(macro_coverage=()))
    assert decision(g).code == "macro_calendar_missing"
    assert decision(g, quantity=-20).action is Action.ALLOW
    g = gate(supply(macro_coverage=(proof(), proof(evidence_id="conflicting"))))
    assert decision(g).code == "macro_calendar_missing"


@pytest.mark.parametrize(
    "clock,expected", [("09:59", Action.DEFER), ("10:00", Action.DEFER), ("11:00", Action.ALLOW)]
)
def test_adjacent_calendars_cover_existing_gate_entire_session(clock, expected):
    boundary = at("2026-10-12", "10:00")
    proofs = (
        proof(covered_until=boundary),
        proof(evidence_id="second", covered_from=boundary),
    )
    event = macro(event_type="ISM", scheduled_at=boundary)
    g = gate(supply(macro_events=(event,), macro_coverage=proofs))
    assert decision(g, clock).action is expected


@pytest.mark.parametrize("clock", ["09:59", "10:00", "11:00"])
def test_microsecond_gap_in_required_session_rejects_buys_but_allows_sell(clock):
    boundary = at("2026-10-12", "10:00")
    proofs = (
        proof(covered_until=boundary),
        proof(evidence_id="second", covered_from=boundary + timedelta(microseconds=1)),
    )
    g = gate(supply(macro_events=(), macro_coverage=proofs))
    assert decision(g, clock).code == "macro_calendar_missing"
    assert decision(g, clock, quantity=-20).action is Action.ALLOW


@pytest.mark.parametrize("gap", [False, True])
def test_month_boundary_fomc_next_session_requires_continuous_previous_session(gap):
    boundary = at("2026-11-01", "00:00")
    s = replace(macro().source, ingested_at=at("2026-12-31"))
    proofs = (
        proof(covered_until=boundary - timedelta(microseconds=int(gap)), source=s),
        proof(
            evidence_id="november",
            covered_from=boundary,
            covered_until=at("2026-12-01", "00:00"),
            source=s,
        ),
    )
    event = macro(event_type="FOMC_STATEMENT", scheduled_at=at("2026-10-30", "14:00"), source=s)
    g = gate(supply(macro_events=(event,), macro_coverage=proofs), macro_fomc_next_session=True)
    result = decision(g, "10:00", "2026-11-02")
    if gap:
        assert result.code == "macro_calendar_missing"
    else:
        assert result.action is Action.DEFER
        assert datetime.fromisoformat(result.data["resume_at"]) == at("2026-11-02", "10:30")
        assert decision(g, "10:30", "2026-11-02").action is Action.ALLOW
    assert decision(g, "10:00", "2026-11-02", quantity=-20).action is Action.ALLOW


@pytest.mark.parametrize(
    "study,event_day,event_clock,boundary",
    [
        ("2025-11-03", "2025-10-31", "14:00", "2025-11-01"),
        ("2025-03-10", "2025-03-07", "14:00", "2025-03-09"),
        ("2025-12-01", "2025-11-28", "12:50", "2025-12-01"),
        ("2025-12-01", "2025-11-28", "14:00", "2025-12-01"),
    ],
)
@pytest.mark.parametrize("gap", [False, True])
def test_actual_pr8_fomc_next_session_across_dst_month_and_halfday(
    study, event_day, event_clock, boundary, gap
):
    calendar = SessionCalendar()
    known = at("2025-01-01")
    source = replace(macro().source, ingested_at=at("2026-12-31"))
    event = macro(
        event_type="FOMC_STATEMENT",
        scheduled_at=at(event_day, event_clock),
        historically_known_at=known,
        published_at=known,
        source=source,
    )
    opening = calendar.session(datetime.fromisoformat(event_day).date()).market_open
    close = calendar.session(datetime.fromisoformat(study).date()).market_close
    shared = {"historically_known_at": known, "published_at": known, "source": source}
    proofs = (
        proof(
            **shared,
            covered_from=opening,
            covered_until=at(boundary) - timedelta(microseconds=int(gap)),
        ),
        proof(**shared, evidence_id="current", covered_from=at(boundary), covered_until=close),
    )
    g = gate(supply(macro_events=(event,), macro_coverage=proofs), macro_fomc_next_session=True)
    result = decision(g, "09:31", study)
    if gap:
        assert result.action is Action.REJECT and result.code == "macro_calendar_missing"
    else:
        assert result.action is Action.DEFER
        assert datetime.fromisoformat(result.data["resume_at"]) == at(study, "10:30")
    assert decision(g, "09:31", study, quantity=-20).action is Action.ALLOW


def test_cross_month_pit_split_reaches_pinned_pr8_processor_once():
    p = pit_fixture.provider([pit_fixture.event()], proofs=pit_fixture.monthly_proofs())
    broker, _, _, _, _ = build()
    broker.corporate_action_processor = CorporateActionProcessor(broker, p)
    held(broker)
    pit_fixture.tick(broker)
    pit_fixture.tick(broker, at("2026-11-02"), price=50)
    pit_fixture.tick(broker, at("2026-11-03"), price=50)
    assert broker.positions["A"].quantity == 40 and broker.cash == 8000
    assert len(broker.corporate_action_processor.state["records"]) == 1


def test_cross_month_pit_dividend_credit_reaches_pinned_pr8_ledger_once():
    p = pit_fixture.provider(
        pit_fixture.cross_month_dividend_credit(), proofs=pit_fixture.monthly_proofs()
    )
    broker, _, _, _, _ = build()
    broker.corporate_action_processor = CorporateActionProcessor(broker, p)
    held(broker)
    pit_fixture.tick(broker, at("2026-10-29"))
    pit_fixture.tick(broker, at("2026-10-30"), price=99)
    assert broker.account._receivable_value == 20 and broker.cash == 8000
    pit_fixture.tick(broker, at("2026-11-02"), price=99)
    pit_fixture.tick(broker, at("2026-11-03"), price=99)
    assert broker.account._receivable_value == 0 and broker.cash == 8020


@pytest.mark.parametrize(
    "day,clock,end",
    [
        ("2026-03-06", "08:30", "10:30"),
        ("2026-03-09", "08:30", "10:30"),
        ("2026-11-27", "12:50", "13:00"),
        ("2026-10-11", "10:00", None),
    ],
)
def test_existing_calendar_dst_halfday_weekend(day, clock, end):
    known, ingested = at("2026-01-01"), at("2026-12-31")
    e = macro(
        event_type="ISM" if clock != "08:30" else "CPI",
        scheduled_at=at(day, clock),
        historically_known_at=known,
        published_at=known,
        source=replace(macro().source, ingested_at=ingested),
    )
    c = proof(
        covered_from=known,
        covered_until=ingested,
        historically_known_at=known,
        published_at=known,
        source=e.source,
    )
    g = gate(supply(macro_events=(e,), macro_coverage=(c,)))
    if end:
        now = "12:40" if clock == "12:50" else "10:00"
        assert datetime.fromisoformat(decision(g, now, day).data["resume_at"]) == at(day, end)
    else:
        assert decision(g, "10:00", day).action is Action.ALLOW


@pytest.mark.parametrize("country,rate_value,net", [("US", 0.10, 18), ("CA", 0.15, 17)])
def test_existing_tax_resolver_and_ledger_once(country, rate_value, net):
    p = supply(
        tax_sources=(tax_source(source_country=country),),
        tax_rules=(rule(source_country=country, rate=rate_value),),
    )
    original = action("CASH_DIVIDEND", "dividend")
    snapshot = tax(p)
    dividend, r, q = snapshot.adapt(
        original, rule_factory=DividendTaxRule, qualification_factory=DividendTaxQualification
    )
    config = replace(
        ConstraintConfig(),
        dividend_tax_profile="cn_mainland_individual_treaty",
        dividend_tax_scenario="synthetic",
        dividend_tax_rules=(r,),
        dividend_tax_qualification=q,
    )
    assert resolve_dividend_tax_rate(config, dividend, at("2026-10-12"))[0] == rate_value
    credit = action("CASH_CREDIT", "credit", "2026-10-13", parent_event_id="dividend")
    broker, _, _, _, _ = build(dividend, credit, settings=config)
    held(broker)
    tick(broker)
    tick(broker, at("2026-10-12"), price=99)
    assert broker.account._receivable_value == net and broker.cash == 8000
    tick(broker, at("2026-10-13"), price=99)
    tick(broker, at("2026-10-13"), price=99)
    assert broker.account._receivable_value == 0 and broker.cash == 8000 + net


def test_account_withholding_preserved_and_existing_conflict_policy():
    original = action(
        "CASH_DIVIDEND",
        "dividend",
        withholding_rate=0.12,
        withholding_source="account:fixture",
        withholding_known_at=at("2026-10-01"),
    )
    event, r, q = tax().adapt(
        original, rule_factory=DividendTaxRule, qualification_factory=DividendTaxQualification
    )
    config = replace(
        ConstraintConfig(),
        dividend_tax_profile="cn_mainland_individual_treaty",
        dividend_tax_scenario="synthetic",
        dividend_tax_rules=(r,),
        dividend_tax_qualification=q,
    )
    assert resolve_dividend_tax_rate(config, event, at("2026-10-12"))[0] == 0.12
    with pytest.raises(ValueError, match="conflicts"):
        resolve_dividend_tax_rate(
            replace(config, dividend_tax_conflict_policy="reject"), event, at("2026-10-12")
        )


def test_late_qualification_fails_before_ledger():
    p = supply(qualifications=(qualification(provider_available_at=at("2026-10-13")),))
    with pytest.raises(ValueError, match="PIT_TAX_UNKNOWN"):
        tax(p)


@pytest.mark.parametrize("country,rate_value,net", [("US", 0.10, 18), ("CA", 0.15, 17)])
def test_pinned_tax_ledger_engine_with_real_order_fee_and_credit(country, rate_value, net):
    """Actual Engine callbacks buy the eligible shares; no seeded position/account."""
    runtime = importlib.import_module("ml4t.backtest")
    p = supply(
        tax_sources=(tax_source(source_country=country),),
        tax_rules=(rule(source_country=country, rate=rate_value),),
    )
    dividend, r, q = tax(p).adapt(
        action("CASH_DIVIDEND", "dividend"),
        rule_factory=DividendTaxRule,
        qualification_factory=DividendTaxQualification,
    )
    config = replace(
        ConstraintConfig(),
        dividend_tax_profile="cn_mainland_individual_treaty",
        dividend_tax_scenario="synthetic",
        dividend_tax_rules=(r,),
        dividend_tax_qualification=q,
    )
    credit = action("CASH_CREDIT", "credit", "2026-10-13", parent_event_id="dividend")
    _, controller, provider, backtest, context = build(
        dividend, credit, settings=config, costs=True
    )
    stamps = [
        at("2026-10-09", "10:30"),
        at("2026-10-09", "10:31"),
        at("2026-10-12", "10:30"),
        at("2026-10-12", "10:31"),
        at("2026-10-13", "10:30"),
        at("2026-10-13", "10:31"),
    ]
    feed = runtime.DataFeed(
        prices_df=pl.DataFrame(
            {
                "timestamp": stamps,
                "asset": ["A"] * 6,
                "open": [100.0] * 6,
                "high": [101.0] * 6,
                "low": [99.0] * 6,
                "close": [100.0] * 6,
                "volume": [10000.0] * 6,
            }
        )
    )

    class DeclaredBuy(runtime.Strategy):
        def __init__(self):
            self.rows = {}

        def on_data(self, timestamp, data, context, broker):
            self.rows[timestamp] = (broker.cash, broker.account._receivable_value)
            if timestamp == stamps[0]:
                broker.submit_order("A", 20)

    probe = DeclaredBuy()
    # The immutable sibling exposes its own Broker extensions, absent on the host base.
    engine_factory = cast(Any, quant_constraints.constrained_engine)
    engine = engine_factory(
        feed, probe, controller, context, backtest, corporate_action_provider=provider
    )
    result = engine.run()
    assert len(result.fills) == len(engine.broker.fee_records) == 1
    assert engine.broker.positions["A"].quantity == 20
    bought_cash = probe.rows[stamps[1]][0]
    assert probe.rows[stamps[2]] == (bought_cash, net)
    assert probe.rows[stamps[4]] == (bought_cash + net, 0)
    assert engine.broker.cash == bought_cash + net
    assert engine.broker.fee_records[0].fees.total_fees > 0
    evidence = result.metrics["corporate_actions_v1"]
    assert len(evidence["records"]) == 2
    assert evidence["records"][0]["withholding_rate"] == rate_value


@pytest.mark.parametrize("complete", [False, True])
def test_pinned_macro_engine_actual_buy_and_risk_sell_do_not_bypass_calendar(complete):
    runtime = importlib.import_module("ml4t.backtest")
    p = supply(
        macro_events=(
            macro(),
            macro(
                evidence_id="statement",
                event_type="FOMC_STATEMENT",
                scheduled_at=at("2026-10-12", "14:00"),
            ),
        ),
        macro_coverage=(proof(),) if complete else (),
    )
    config = replace(ConstraintConfig(), macro_events_enabled=True)
    _, ctrl, provider, backtest, context = build(settings=config, costs=True)
    controller_type = cast(Any, quant_constraints.ConstraintController)
    controller: Any = controller_type(
        ctrl.config,
        ctrl.earnings.provider,
        p.macro_adapter(event_factory=MacroEvent, coverage_factory=MacroEventCoverage),
    )
    stamps = [at("2026-10-12", c) for c in ("10:00", "10:01", "10:30", "14:15", "14:16")]
    feed = runtime.DataFeed(
        prices_df=pl.DataFrame(
            {
                "timestamp": stamps,
                "asset": ["A"] * 5,
                "open": [100.0] * 5,
                "high": [101.0] * 5,
                "low": [99.0] * 5,
                "close": [100.0] * 5,
                "volume": [10000.0] * 5,
            }
        )
    )

    class DeclaredOrders(runtime.Strategy):
        def __init__(self):
            self.buy = None

        def on_data(self, timestamp, data, context, broker):
            if timestamp == stamps[0]:
                self.buy = broker.submit_order("A", 20)
            if timestamp == stamps[3] and broker.account.get_position_quantity("A") == 20:
                broker.submit_order("A", -20, constraint_kind=quant_constraints.Kind.RISK)

    probe = DeclaredOrders()
    engine_factory = cast(Any, quant_constraints.constrained_engine)
    engine = engine_factory(
        feed, probe, controller, context, backtest, corporate_action_provider=provider
    )
    result = engine.run()
    if complete:
        assert len(result.fills) == len(engine.broker.fee_records) == 2
        assert result.fills[0].timestamp == stamps[2]
        assert result.fills[1].timestamp == stamps[4]
        assert any(a.macro_checked and a.decision.action is Action.DEFER for a in controller.audit)
        assert not engine.broker.positions and engine.broker.unsettled_cash > 0
    else:
        assert not result.fills and not engine.broker.positions and not engine.broker.fee_records
        assert engine.broker.cash == engine.broker.settled_cash == 10000
        assert probe.buy is not None
        assert probe.buy.rejection_code == "macro_calendar_missing"
