"""Invented policy inputs; broker fills and the canonical dividend ledger are exercised."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from test_adapter import at, make, market, tick
from test_corporate_actions import action, build, held
from test_gates import context, provider, state

from ml4t.backtest import OrderStatus
from ml4t.backtest.strategies.sector_rotation import SectorRotationConfig
from quant_constraints import (
    Action,
    ConstraintConfig,
    ConstraintController,
    Holding,
    Intent,
    Kind,
)

ROOT = Path(__file__).resolve().parents[2]
STRICT = ConstraintConfig(security_entry_policy="exclude_canada_and_adrs")


def facts(value=None, asset="A", **changes):
    values = {
        "security_id": f"stable-{asset}",
        "issuer_country": "US",
        "tax_source_country": "US",
        "security_type": "CS",
        "is_depositary_receipt": False,
        "security_metadata_available_at": at("2026-10-08"),
        "security_metadata_source": "https://example.invalid/invented-identity",
        "security_metadata_sha256": "a" * 64,
        "security_metadata_symbol": asset,
        "security_metadata_security_id": f"stable-{asset}",
    }
    values.update(changes)
    return replace(value or context(), **values)


def decision(market_context, kind=Kind.ENTRY, quantity=20, snapshot=None):
    ctrl = ConstraintController(STRICT, provider())
    result = ctrl.check(Intent("A", quantity, kind=kind), snapshot or state(), market_context)
    assert ctrl.audit[-1].decision == result
    return result


@pytest.mark.parametrize("kind", [Kind.ENTRY, Kind.ADD, Kind.REBALANCE])
@pytest.mark.parametrize("country_field", ["issuer_country", "tax_source_country"])
def test_canadian_dual_listed_common_stock_is_rejected_for_every_buy(kind, country_field):
    # USD quote, US venue and ordinary CS type do not establish issuer domicile.
    assert decision(facts(**{country_field: "CA"}), kind).code == "canadian_security_excluded"


@pytest.mark.parametrize("security_type", ["ADR", "ADS", "ADRC", "ADRP", "ADRW", "ADRR", "ADRU"])
def test_all_depositary_receipt_variants_are_rejected(security_type):
    assert decision(facts(security_type=security_type)).code == "adr_security_excluded"
    assert decision(facts(is_depositary_receipt=True)).code == "adr_security_excluded"


@pytest.mark.parametrize(
    "field,value",
    [
        ("issuer_country", None),
        ("issuer_country", "ZZ"),
        ("tax_source_country", None),
        ("is_depositary_receipt", None),
        ("is_depositary_receipt", 0),
        ("security_type", None),
        ("security_type", "STK"),
        ("security_type", []),
        ("security_id", None),
        ("security_metadata_available_at", None),
        ("security_metadata_available_at", at() + timedelta(seconds=1)),
        ("security_metadata_sha256", None),
        ("security_metadata_sha256", "invalid"),
        ("security_metadata_source", "http://example.invalid/identity"),
        ("security_metadata_source", "https://user:password@example.invalid/identity"),
    ],
)
def test_unknown_incomplete_or_future_metadata_cannot_create_a_buy(field, value):
    result = decision(facts(**{field: value}))
    assert result.action is Action.REJECT and result.code == "security_metadata_unverified"


@pytest.mark.parametrize("field", ["security_metadata_symbol", "security_metadata_security_id"])
def test_metadata_for_another_security_cannot_be_reused(field):
    assert decision(facts(**{field: "other"})).code == "security_identity_conflict"


def test_common_stock_and_plain_etf_pass_only_the_matching_instrument_contract():
    assert decision(facts()).action is Action.ALLOW
    assert (
        decision(facts(instrument="plain_sector_etf", security_type="ETF")).action is Action.ALLOW
    )
    assert decision(facts(security_type="ETF")).code == "security_type_conflict"


@pytest.mark.parametrize("kind", [Kind.REDUCE, Kind.RISK])
@pytest.mark.parametrize("changes", [{"issuer_country": "CA"}, {"is_depositary_receipt": True}, {}])
def test_existing_positions_can_exit_even_when_ineligible_or_metadata_missing(kind, changes):
    market_context = replace(context(), **changes)
    snapshot = state(holdings={"A": Holding(20, 100, "technology", at(), "equity")})
    assert decision(market_context, kind, -20, snapshot).action is Action.ALLOW


def test_legacy_api_keeps_unknown_fixture_compatibility_but_rejects_explicit_exclusions():
    ctrl = ConstraintController(ConstraintConfig(), provider())
    assert ctrl.check(Intent("A", 20), state(), context()).action is Action.ALLOW
    assert ctrl.check(Intent("A", 20), state(), facts(issuer_country="CA")).action is Action.REJECT
    assert ctrl.check(Intent("A", 20), state(), facts(security_type="ADRC")).action is Action.REJECT


def test_fill_revalidation_cancels_a_pending_buy_without_cash_or_position_changes():
    broker, ctrl = make(settings=STRICT)
    broker.context_provider = lambda ts, asset, phase: facts(
        market(ts, asset, phase), asset, issuer_country="CA" if ts > at() else "US"
    )
    tick(broker)
    order = broker.submit_order("A", 20)
    assert order.status is OrderStatus.PENDING and broker.reserved_cash == 2000
    tick(broker, at(clock="10:31"))
    broker._process_orders(use_open=True)
    assert order.status is OrderStatus.REJECTED
    assert order._rejection_code == "canadian_security_excluded"
    assert broker.cash == broker.settled_cash == 10000 and broker.reserved_cash == 0
    assert not broker.positions and not broker.fills
    assert any(a.phase == "fill" and a.decision.code == order._rejection_code for a in ctrl.audit)


def test_existing_adr_can_sell_and_dividend_credit_uses_entitlement_after_sale():
    broker, _, _, _, _ = build(
        action("CASH_DIVIDEND"),
        action("CASH_CREDIT", "credit", "2026-10-14", parent_event_id="event", credited_net=20),
        settings=STRICT,
    )
    original = broker.context_provider
    broker.context_provider = lambda ts, asset, phase: facts(
        original(ts, asset, phase), asset, security_type="ADRC", is_depositary_receipt=True
    )
    held(broker)
    tick(broker)
    tick(broker, at("2026-10-12"), price=99)
    assert broker.account._receivable_value == 20
    assert broker.submit_order("A", 1)._rejection_code == "adr_security_excluded"
    sale = broker.submit_order("A", -20)
    tick(broker, at("2026-10-12", "10:31"), price=99)
    broker._process_orders(use_open=True)
    assert sale.status is OrderStatus.FILLED and not broker.positions
    tick(broker, at("2026-10-14"), price=99)
    assert broker.cash == 10000 and broker.account._receivable_value == 0
    tick(broker, at("2026-10-14"), price=99)
    assert broker.cash == 10000


def test_owner_configs_enable_exclusion_and_do_not_default_to_canadian_treaty():
    assert ConstraintConfig.from_yaml(
        ROOT / "config/us_cash_concentrated.yaml"
    ).security_entry_policy == ("exclude_canada_and_adrs")
    tax = ConstraintConfig.from_yaml(ROOT / "config/cn_mainland_ordinary_dividend.yaml")
    assert tax.security_entry_policy == "exclude_canada_and_adrs"
    assert {rule.source_country for rule in tax.dividend_tax_rules} == {"US"}
    assert tax.dividend_tax_qualification is None
    assert SectorRotationConfig().constraints().security_entry_policy == "exclude_canada_and_adrs"


def test_invalid_policy_and_naive_observation_time_rejected():
    with pytest.raises(ValueError, match="security_entry_policy"):
        replace(STRICT, security_entry_policy="allow_all")
    with pytest.raises(ValueError, match="timezone-aware"):
        facts(security_metadata_available_at=at().replace(tzinfo=None))
