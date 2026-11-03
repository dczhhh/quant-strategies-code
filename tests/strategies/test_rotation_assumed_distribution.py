"""Explicit invented 2024 classification scenario; never issuer/PIT/tax certification."""

import json
from dataclasses import asdict, replace
from datetime import date

import pytest

from ml4t.backtest.strategies.sector_rotation.research import (
    ResearchAssumptions,
    retrospective_rotation_engine,
)
from ml4t.backtest.strategies.sector_rotation.research_inputs import (
    ASSUMED_DISTRIBUTION_SCHEMA,
    ResearchInputs,
    ResearchSource,
    digest,
)
from tests.strategies.test_rotation_research import ASSUMPTIONS, BODY, CAL, SOURCE, inputs, settings
from tools import rotation_stress_inventory as s


def assumed_inputs():
    old = inputs()
    days = sorted({r.session for r in old.closes} | set(old.settlement_sessions))
    mapped = {day: CAL.shift(date(2024, 9, 3), index) for index, day in enumerate(days)}

    def clock(stamp):
        original, current = CAL.session(stamp.date()), CAL.session(mapped[stamp.date()])
        return (
            current.market_close
            if stamp == original.market_close
            else current.market_open + (stamp - original.market_open)
        )

    assumption = ResearchSource(
        "assumption:2024",
        SOURCE.sha256,
        SOURCE.observed_at,
        "https://example.invalid/explicit-2024-scenario",
    )
    return replace(
        old,
        sources=(*old.sources, assumption),
        closes=tuple(replace(r, session=mapped[r.session]) for r in old.closes),
        bars=tuple(replace(b, end=clock(b.end)) for b in old.bars),
        distributions=tuple(
            replace(
                e,
                ex_date=mapped[e.ex_date],
                record_date=mapped[e.record_date],
                pay_date=mapped[e.pay_date],
                classification="assumed_ordinary_fund",
                classification_source_id=assumption.identifier,
            )
            for e in old.distributions
        ),
        splits=tuple(replace(e, session=mapped[e.session]) for e in old.splits),
        settlement_sessions=tuple(mapped[d] for d in old.settlement_sessions),
    )


def test_missing_2024_final_classification_cannot_silently_enter_default_research():
    bundle = assumed_inputs()
    assert bundle.bundle_schema == ASSUMED_DISTRIBUTION_SCHEMA
    with pytest.raises(ValueError, match="explicit year-bound"):
        retrospective_rotation_engine(bundle, ASSUMPTIONS, settings())


def test_separately_enabled_2024_assumption_runs_but_remains_explicit_and_non_pit():
    bundle = assumed_inputs()
    assumptions = replace(
        ASSUMPTIONS,
        name="invented explicitly assumed 2024 ordinary-fund classification",
        assumed_ordinary_years=(2024,),
    )
    engine = retrospective_rotation_engine(bundle, assumptions, settings())
    engine.run()
    assert (
        engine.research_report["schema"] == "rotation_retrospective_engine_v3_assumed_distribution"
    )
    assert (
        engine.research_report["PIT_verified"]
        is engine.research_report["performance_admission"]
        is False
    )
    assert engine.research_report["full_real_scenarios_accepted"] == 0
    records = engine.broker.corporate_action_evidence()["records"]
    assert any(r["status"] == "dividend_receivable" for r in records)


@pytest.mark.parametrize("years", [(2025,), (2024, 2025), (True,), (2024, 2024)])
def test_assumption_cannot_expand_years_or_coerce_boolean(years):
    with pytest.raises(ValueError, match="2024 ordinary"):
        replace(ASSUMPTIONS, assumed_ordinary_years=years)


def test_scenario_source_must_not_impersonate_an_issuer_document():
    bundle = assumed_inputs()
    bundle = replace(
        bundle,
        distributions=tuple(
            replace(e, classification_source_id="fixture") for e in bundle.distributions
        ),
    )
    with pytest.raises(ValueError, match="explicit year-bound"):
        retrospective_rotation_engine(
            bundle, replace(ASSUMPTIONS, assumed_ordinary_years=(2024,)), settings()
        )


def test_assumed_distribution_cannot_hide_behind_retained_market_origin():
    with pytest.raises(ValueError, match="explicit research origin"):
        replace(assumed_inputs(), origin="retained_market_data")


def test_new_schema_cannot_be_hidden_inside_an_old_bundle():
    bundle = assumed_inputs()
    doc = {"schema": bundle.bundle_schema, **asdict(bundle)}
    bodies = {source.identifier: BODY for source in bundle.sources}
    body = json.dumps(doc, default=str).encode()
    assert ResearchInputs.from_bundle(body, digest(body), bodies) == bundle
    for schema in ("rotation_retrospective_inputs_v1", "rotation_retrospective_inputs_v2"):
        doc["schema"] = schema
        body = json.dumps(doc, default=str).encode()
        with pytest.raises(ValueError, match="explicit v3"):
            ResearchInputs.from_bundle(body, digest(body), bodies)


def test_frozen_g1_and_capture_contract_remain_identical_after_additive_assumption_field():
    assert s.CONTRACT_HASH == "f75c457ad9908ae9d30074c98c23a6d89e3ab52b212f8d93fefe2a8dafbc40d1"
    assert "assumed_ordinary_years" not in s.CONTRACT["assumptions"]
    assert ResearchAssumptions(**asdict(ASSUMPTIONS)).assumed_ordinary_years == ()
