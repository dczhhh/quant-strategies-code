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


def test_tax_yaml_restores_immutable_rules_and_typed_qualification(tmp_path):
    from dataclasses import asdict

    import yaml

    original = scenario()
    path = tmp_path / "invented-tax.yaml"
    path.write_text(yaml.safe_dump(asdict(original)), encoding="utf-8")
    assert ConstraintConfig.from_yaml(path) == original


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
