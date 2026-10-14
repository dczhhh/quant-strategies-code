"""Invented fixtures exercise the production path; none count as real account evidence."""

import copy
from dataclasses import replace
from datetime import date

import pytest
from test_adapter import at, tick
from test_corporate_actions import action, build, held

from quant_constraints import (
    ConstraintConfig,
    DividendTaxQualification,
    DividendTaxRule,
    resolve_dividend_tax_rate,
)


def scenario(country="US", **changes):
    rule = DividendTaxRule(
        country,
        {"US": 0.10, "CA": 0.15}[country],
        date(2026, 1, 1),
        date(2027, 1, 1),
        at("2026-01-01"),
        at("2026-01-01"),
        "invented:treaty-source",
        "invented-v1",
    )
    qualification = DividendTaxQualification(
        "CN-mainland",
        True,
        True,
        date(2026, 1, 1),
        date(2027, 1, 1),
        at("2026-01-01"),
        "invented:qualification",
        non_us_ca_tax_resident=True,
        individual=True,
    )
    return replace(
        ConstraintConfig(),
        dividend_tax_profile="cn_mainland_individual_treaty",
        dividend_tax_scenario="invented-qualified-individual",
        dividend_tax_rules=(rule,),
        dividend_tax_qualification=qualification,
        **changes,
    )


def dividend(country="US", **changes):
    return action(
        "CASH_DIVIDEND",
        tax_source_country=country,
        tax_source="invented:issuer-country",
        tax_source_known_at=at("2026-01-01"),
        distribution_type="ordinary_stock",
        **changes,
    )


def credit(**changes):
    terms = {
        "credited_gross": 20.0,
        "credited_withheld": 2.0,
        "credited_fee": 0.0,
        "credited_net": 18.0,
    }
    terms.update(changes)
    return action("CASH_CREDIT", "credit", "2026-10-13", parent_event_id="event", **terms)


@pytest.mark.parametrize("country,rate,net", [("US", 0.10, 18.0), ("CA", 0.15, 17.0)])
def test_production_country_resolver_and_unique_ledger(country, rate, net):
    broker, _, _, _, _ = build(
        dividend(country),
        credit(credited_withheld=20 * rate, credited_net=net),
        settings=scenario(country),
    )
    held(broker)
    tick(broker)
    tick(broker, at("2026-10-12"), price=99)
    assert broker.cash == 8000 and broker.account._receivable_value == net
    assert broker.get_account_value() == pytest.approx(8000 + 20 * 99 + net)
    tick(broker, at("2026-10-13"), price=99)
    tick(broker, at("2026-10-13"), price=99)
    assert broker.cash == 8000 + net and broker.account._receivable_value == 0
    proof = broker.corporate_action_evidence()
    assert proof["withholding"] == pytest.approx(20 * rate)
    assert proof["records"][-1]["withholding_applied_again"] is False
    assert proof["capital_gains_tax_mode"] == "none"


def test_usd_does_not_make_canadian_issuer_us_tax_source():
    config, event = scenario("CA"), dividend("CA")
    assert event.currency == "USD"
    rate, proof = resolve_dividend_tax_rate(config, event, at("2026-10-12"))
    assert rate == 0.15 and proof["source_country"] == "CA"


@pytest.mark.parametrize(
    "field,value",
    [
        ("tax_source_country", None),
        ("tax_source", None),
        ("tax_source_known_at", None),
        ("tax_source_known_at", at("2026-10-13")),
        ("distribution_type", None),
        ("distribution_type", "capital_gain"),
        ("distribution_type", "return_of_capital"),
        ("distribution_type", "special"),
        ("currency", "CAD"),
        ("terms", "special"),
    ],
)
def test_unknown_source_or_distribution_rejected(field, value):
    with pytest.raises(ValueError, match="unavailable or unsupported"):
        resolve_dividend_tax_rate(
            scenario(), replace(dividend(), **{field: value}), at("2026-10-12")
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("tax_residence", "US"),
        ("beneficial_owner", False),
        ("treaty_documents_valid", False),
        ("non_us_ca_tax_resident", False),
        ("individual", False),
        ("known_at", at("2026-10-13")),
        ("effective_to", date(2026, 10, 12)),
    ],
)
def test_treaty_qualification_required(field, value):
    config = scenario()
    qualification = replace(config.dividend_tax_qualification, **{field: value})
    with pytest.raises(ValueError, match="eligibility"):
        resolve_dividend_tax_rate(
            replace(config, dividend_tax_qualification=qualification), dividend(), at("2026-10-12")
        )


@pytest.mark.parametrize("missing", [None, "rule-future", "rule-expired", "rule-conflict"])
def test_no_invented_historical_tax_rule(missing):
    config = scenario()
    if missing is None:
        config = replace(config, dividend_tax_qualification=None)
    else:
        rule = config.dividend_tax_rules[0]
        if missing == "rule-future":
            rule = replace(rule, known_at=at("2026-10-13"), verified_at=at("2026-10-13"))
        elif missing == "rule-expired":
            rule = replace(rule, effective_to=date(2026, 10, 12))
        rules = (rule, rule) if missing == "rule-conflict" else (rule,)
        config = replace(config, dividend_tax_rules=rules)
    with pytest.raises(ValueError):
        resolve_dividend_tax_rate(config, dividend(), at("2026-10-12"))


def test_actual_account_withholding_wins_without_faking_qualification():
    event = dividend(
        withholding_rate=0.12,
        withholding_source="invented:account-credit",
        withholding_known_at=at("2026-10-01"),
    )
    config = replace(scenario(), dividend_tax_qualification=None)
    rate, proof = resolve_dividend_tax_rate(config, event, at("2026-10-12"))
    assert rate == 0.12 and proof["scenario_rate_conflict"] is True
    with pytest.raises(ValueError, match="conflicts"):
        resolve_dividend_tax_rate(
            replace(config, dividend_tax_conflict_policy="reject"), event, at("2026-10-12")
        )


def test_actual_credit_components_reconcile_estimate_without_double_tax():
    broker, _, _, _, _ = build(
        dividend(),
        credit(credited_withheld=2.4, credited_fee=0.1, credited_net=17.5),
        settings=scenario(),
    )
    held(broker)
    tick(broker)
    tick(broker, at("2026-10-12"), price=99)
    equity = broker.get_account_value()
    tick(broker, at("2026-10-13"), price=99)
    proof = broker.corporate_action_evidence()
    assert broker.cash == 8017.5 and proof["withholding"] == pytest.approx(2.4)
    assert broker.get_account_value() == pytest.approx(equity - 0.5)
    assert proof["records"][-1]["reconciled_net_difference"] == -0.5


def correction_credit(**changes):
    """Wholly invented account correction; never publish user statement amounts."""
    terms = {
        "credited_gross": 19.5,
        "credited_withheld": 1.95,
        "credited_fee": 0.05,
        "credited_net": 17.5,
        "credit_correction_of_version": 1,
        "credit_correction_prior_gross": 20.0,
        "credit_correction_quantity": 20.0,
        "credit_correction_source": "invented:account-gross-correction",
        "credit_correction_known_at": at("2026-10-13"),
    }
    terms.update(changes)
    return credit(**terms)


@pytest.mark.parametrize("gross,tax,fee,net", [(19.5, 1.95, 0.05, 17.5), (25.0, 2.5, 0.0, 22.5)])
def test_sourced_gross_correction_reconciles_locked_entitlement_once(gross, tax, fee, net):
    corrected = correction_credit(
        credited_gross=gross, credited_withheld=tax, credited_fee=fee, credited_net=net
    )
    broker, _, _, _, _ = build(dividend(), corrected, settings=scenario())
    held(broker)
    tick(broker)
    tick(broker, at("2026-10-12"), price=99)
    equity = broker.get_account_value()
    tick(broker, at("2026-10-13"), price=99)
    tick(broker, at("2026-10-13"), price=99)
    proof = broker.corporate_action_evidence()
    assert broker.cash == 8000 + net and broker.account._receivable_value == 0
    assert broker.get_account_value() == pytest.approx(equity + net - 18)
    assert proof["income"] == pytest.approx(net)
    assert proof["withholding"] == pytest.approx(tax)
    item = broker.corporate_action_processor.state["entitlements"][("stable-A", "event")]
    assert item["gross"] == 20 and item["net"] == 18 and item["quantity"] == 20
    record = proof["records"][-1]
    assert record["credit_correction"]["prior_version"] == 1
    assert record["reconciled_gross_difference"] == gross - 20
    assert record["withholding_applied_again"] is False


@pytest.mark.parametrize(
    "changes",
    [
        {"credit_correction_of_version": 2},
        {"credit_correction_prior_gross": 21.0},
        {"credit_correction_quantity": 21.0},
    ],
)
def test_correction_bound_to_wrong_original_terms_fails_atomically(changes):
    broker, _, _, _, _ = build(dividend(), correction_credit(**changes), settings=scenario())
    held(broker)
    tick(broker)
    tick(broker, at("2026-10-12"), price=99)
    before = copy.deepcopy((broker.cash, broker.account._receivables, broker.positions))
    with pytest.raises(ValueError, match="bind the original"):
        tick(broker, at("2026-10-13"), price=99)
    assert (broker.cash, broker.account._receivables, broker.positions) == before


@pytest.mark.parametrize(
    "changes",
    [
        {"credit_correction_of_version": None},
        {"credit_correction_of_version": True},
        {"credit_correction_of_version": 0},
        {"credit_correction_prior_gross": None},
        {"credit_correction_quantity": None},
        {"credit_correction_quantity": 0},
        {"credit_correction_quantity": float("nan")},
        {"credit_correction_source": None},
        {"credit_correction_source": ""},
        {"credit_correction_known_at": None},
        {"credit_correction_known_at": at("2026-10-14")},
        {"credited_gross": None},
    ],
)
def test_incomplete_or_future_correction_evidence_is_not_an_admission_flag(changes):
    with pytest.raises(ValueError):
        correction_credit(**changes)


def test_correction_components_must_still_reconcile():
    broker, _, _, _, _ = build(
        dividend(), correction_credit(credited_net=18.0), settings=scenario()
    )
    held(broker)
    tick(broker)
    tick(broker, at("2026-10-12"), price=99)
    with pytest.raises(ValueError, match="components do not reconcile"):
        tick(broker, at("2026-10-13"), price=99)
    assert broker.cash == 8000 and broker.account._receivable_value == 18


def test_direct_credit_rejects_future_visibility_and_missing_receivable_before_cash_mutation():
    broker, _, _, _, _ = build(dividend(), settings=scenario())
    held(broker)
    tick(broker)
    tick(broker, at("2026-10-12"), price=99)
    processor = broker.corporate_action_processor
    with pytest.raises(ValueError, match="not visible"):
        processor.credit(correction_credit(), {"processed_at": at("2026-10-12").isoformat()})
    assert broker.cash == 8000 and broker.account._receivable_value == 18
    broker.account._receivables.clear()
    with pytest.raises(ValueError, match="checkpoint does not reconcile"):
        processor.credit(correction_credit(), {"processed_at": at("2026-10-13").isoformat()})
    assert broker.cash == 8000


def test_revised_paid_correction_rejected_without_crediting_again():
    initial = correction_credit()
    revised = replace(
        initial, version=2, available_at=at("2026-10-14"), credited_net=17.6, credited_gross=19.6
    )
    broker, _, _, _, _ = build(dividend(), initial, revised, settings=scenario())
    held(broker)
    tick(broker)
    tick(broker, at("2026-10-12"), price=99)
    tick(broker, at("2026-10-13"), price=99)
    with pytest.raises(ValueError, match="Applied corporate action revised"):
        tick(broker, at("2026-10-14"), price=99)
    assert broker.cash == 8017.5 and broker.account._receivable_value == 0


def test_reorganization_cannot_be_replayed_as_an_ordinary_split():
    # Source-derived event classification is exercised with invented quantity,
    # dates and prices. This is not a WOLF price/history/account replay.
    broker, _, _, _, _ = build(action(kind="REORGANIZATION", terms="special"))
    held(broker, quantity=80)
    tick(broker)
    before = copy.deepcopy((broker.cash, broker.positions, broker.orders))
    with pytest.raises(ValueError, match="Unsupported exposed corporate action"):
        tick(broker, at("2026-10-12"))
    assert (broker.cash, broker.positions, broker.orders) == before


@pytest.mark.parametrize(
    "field", ["credited_gross", "credited_withheld", "credited_fee", "credited_net"]
)
def test_missing_real_credit_components_fail_atomically(field):
    broker, _, _, _, _ = build(dividend(), credit(**{field: None}), settings=scenario())
    held(broker)
    tick(broker)
    tick(broker, at("2026-10-12"), price=99)
    before = copy.deepcopy((broker.cash, broker.account._receivables, broker.positions))
    with pytest.raises(ValueError, match="UNVERIFIED"):
        tick(broker, at("2026-10-13"), price=99)
    assert (broker.cash, broker.account._receivables, broker.positions) == before


@pytest.mark.parametrize(
    "changes",
    [
        {"credited_gross": 21.0},
        {"credited_withheld": 3.0},
        {"credited_fee": 1.0},
        {"credited_net": 19.0},
        {"credited_withheld": 21.0, "credited_net": 0.0},
    ],
)
def test_inconsistent_credit_rejected(changes):
    broker, _, _, _, _ = build(dividend(), credit(**changes), settings=scenario())
    held(broker)
    tick(broker)
    tick(broker, at("2026-10-12"), price=99)
    with pytest.raises(ValueError, match="reconcile"):
        tick(broker, at("2026-10-13"), price=99)
    assert broker.cash == 8000 and broker.account._receivable_value == 18


def test_nonstock_etf_cannot_impersonate_ordinary_stock_tax():
    broker, _, _, _, context = build(dividend(), settings=scenario())
    broker.context_provider = lambda ts, asset, phase: replace(
        context(ts, asset, phase), instrument="plain_sector_etf"
    )
    held(broker)
    tick(broker)
    with pytest.raises(ValueError, match="ETF distributions"):
        tick(broker, at("2026-10-12"), price=99)
    assert broker.cash == 8000 and broker.account._receivable_value == 0


def test_ge_style_cash_in_lieu_rejected_without_phantom_tradable_fraction():
    broker, _, _, _, _ = build(action(split_ratio=0.125, fractional_policy="cash_in_lieu"))
    held(broker, quantity=83, price=100)
    tick(broker)
    before = copy.deepcopy((broker.cash, broker.positions, broker.orders))
    with pytest.raises(ValueError, match="Cash-in-lieu requires authoritative"):
        tick(broker, at("2026-10-12"), price=800)
    assert (broker.cash, broker.positions, broker.orders) == before
    assert broker.positions["A"].quantity == 83


def test_formal_ge_default_retain_cannot_create_virtual_tradable_fraction():
    broker, _, _, _, _ = build(action(split_ratio=0.125), settings=scenario())
    held(broker, quantity=83, price=100)
    tick(broker)
    before = copy.deepcopy((broker.cash, broker.positions, broker.orders))
    with pytest.raises(ValueError, match="UNSUPPORTED_CASH_IN_LIEU"):
        tick(broker, at("2026-10-12"), price=800)
    assert (broker.cash, broker.positions, broker.orders) == before


def test_formal_reverse_split_nonintegral_pending_order_fails_before_reservation_mutation():
    from ml4t.backtest import OrderStatus, OrderType

    config = replace(scenario(), buy_time_in_force="GTD", max_defer_sessions=3)
    broker, _, _, _, _ = build(action(split_ratio=0.125), settings=config)
    tick(broker)
    order = broker.submit_order("A", 20, order_type=OrderType.LIMIT, limit_price=99)
    assert order.status is OrderStatus.PENDING
    before = copy.deepcopy((broker.cash, broker.reserved_cash, broker.orders))
    with pytest.raises(ValueError, match="UNSUPPORTED_CASH_IN_LIEU"):
        tick(broker, at("2026-10-12"), price=800)
    assert (broker.cash, broker.reserved_cash, broker.orders) == before


def test_formal_integral_reverse_split_preserves_cost_and_cash():
    broker, _, _, _, _ = build(action(split_ratio=0.125), settings=scenario())
    position = held(broker, quantity=80, price=100)
    tick(broker)
    before = broker.cash, broker.get_account_value(), position.quantity * position.entry_price
    tick(broker, at("2026-10-12"), price=800)
    assert position.quantity == 10 and position.entry_price == 800
    assert (
        broker.cash,
        broker.get_account_value(),
        position.quantity * position.entry_price,
    ) == before


def test_tax_yaml_restores_immutable_rules_and_typed_qualification(tmp_path):
    from dataclasses import asdict

    import yaml

    original = scenario()
    path = tmp_path / "invented-tax.yaml"
    path.write_text(yaml.safe_dump(asdict(original)), encoding="utf-8")
    assert ConstraintConfig.from_yaml(path) == original


def test_project_ordinary_profile_never_assumes_personal_eligibility_or_early_pit():
    config = ConstraintConfig.from_yaml("config/cn_mainland_ordinary_dividend.yaml")
    assert {rule.source_country: rule.rate for rule in config.dividend_tax_rules} == {
        "US": 0.10,
        "CA": 0.15,
    }
    assert config.dividend_tax_qualification is None
    with pytest.raises(ValueError, match="eligibility unconfirmed"):
        resolve_dividend_tax_rate(config, dividend(), at("2026-10-16"))
    with pytest.raises(ValueError, match="Unknown.*historical rate"):
        resolve_dividend_tax_rate(config, dividend(), at("2026-10-12"))


@pytest.mark.parametrize(
    "changes",
    [
        {"dividend_tax_rules": []},
        {"dividend_tax_rules": (None,)},
        {"dividend_tax_qualification": "confirmed"},
        {"dividend_withholding_rate": 0.1},
        {"dividend_tax_scenario": "gross_no_withholding"},
        {"capital_gains_tax_mode": "fixed"},
        {"unknown_dividend_tax_policy": "guess"},
        {"dividend_tax_conflict_policy": "ignore"},
    ],
)
def test_unsafe_tax_configuration_rejected(changes):
    with pytest.raises(ValueError):
        replace(scenario(), **changes)
