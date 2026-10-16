"""Invented orders only; no private account identifiers, prices or execution rows."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from quant_constraints import FeeExecutionContext, IBKRProTieredUSStock
from quant_constraints.fees.ibkr_fractional_order import IBKRParentOrderFractionalResearch


def context(order="one", day="2026-10-09", side="BUY", **kwargs):
    stamp = datetime.fromisoformat(day + "T10:00:00").replace(tzinfo=ZoneInfo("America/New_York"))
    return FeeExecutionContext(order, stamp, side, **kwargs)


def filled(model, identity, quantity, price=100, ctx=None):
    return model.commit(identity, model.quote(ctx or context(identity), quantity, price))


def model(**kwargs):
    return IBKRParentOrderFractionalResearch(**kwargs)


def test_mixed_order_fractional_tail_does_not_pay_an_extra_percent_or_minimum():
    fees = model()
    ctx = context("mixed", day="2026-09-15")
    whole = filled(fees, "whole", 3, 250, ctx).fees
    tail = filled(fees, "tail", 0.4, 250, ctx).fees
    assert whole.broker_commission == pytest.approx(0.35)
    assert tail.broker_commission == 0
    assert tail.exchange_ecn_fees_or_rebates == 0
    assert tail.total_fees == pytest.approx(0.4 * (0.0002 + 0.000003), abs=1e-12)
    assert tail.total_fees > 0
    assert fees.volume("2026-09") == pytest.approx(3.4)
    assert any("account-observed" in a for a in tail.assumptions)


def test_fractional_sell_still_pays_sec_taf_clearing_and_cat():
    fees = model()
    ctx = context("sale", day="2026-09-15", side="SELL")
    filled(fees, "whole", 4, 225, ctx)
    tail = filled(fees, "tail", 0.3, 225, ctx).fees
    expected = 0.3 * (0.0002 + 0.000003 + 0.000195) + 0.3 * 225 * 0.0000206
    assert tail.total_fees == pytest.approx(expected, abs=1e-12)
    assert tail.total_fees > 0 and tail.broker_commission == 0


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_standalone_fractional_order_is_not_commission_free(side):
    fees = filled(model(), "standalone", 0.6, 180, context("standalone", "2026-09-15", side)).fees
    assert fees.broker_commission == pytest.approx(0.35)
    assert fees.total_fees >= 0.35
    assert fees.exchange_ecn_fees_or_rebates == 0
    assert fees.pass_through_fees == pytest.approx(0.35 * 0.000735)


@pytest.mark.parametrize("parts", [[3.4], [3, 0.4], [0.4, 3], [0.2, 0.2, 3]])
def test_broker_minimum_depends_on_parent_order_not_number_or_order_of_fills(parts):
    fees = model()
    ctx = context("one")
    for i, quantity in enumerate(parts):
        filled(fees, str(i), quantity, 200, ctx)
    assert sum(r.fees.broker_commission for r in fees.records) == pytest.approx(0.35)
    assert fees.volume("2026-10") == pytest.approx(3.4)


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_equivalent_mixed_parent_splits_preserve_all_components_and_separate_orders_do_not(side):
    totals = []
    for parts in ([2.5], [2, 0.5], [1, 1, 0.2, 0.3]):
        fees = model()
        for i, quantity in enumerate(parts):
            filled(fees, str(i), quantity, 200, context("mixed", side=side))
        totals.append(sum(r.fees.total_fees for r in fees.records))
    assert totals == pytest.approx([totals[0]] * 3, abs=1e-12)
    separate = model()
    filled(separate, "first", 2, 200, context("first", side=side))
    filled(separate, "second", 0.5, 200, context("second", side=side))
    assert sum(r.fees.broker_commission for r in separate.records) == pytest.approx(0.7)
    assert sum(r.fees.total_fees for r in separate.records) > totals[0]


def test_fractional_quantities_participate_in_marginal_rate_and_order_cap():
    fees = model(initial_month="2026-10", initial_monthly_volume=299999.8)
    actual = filled(fees, "cross", 200.5, 100).fees
    assert actual.broker_commission == pytest.approx(0.2 * 0.0035 + 200.3 * 0.002)
    assert fees.volume("2026-10") == pytest.approx(300200.3)
    tiny = model()
    charge = filled(tiny, "small", 0.2, 2).fees
    assert charge.broker_commission == pytest.approx(0.004)
    assert charge.total_fees > 0
    assert charge.commission_cap_applied
    assert tiny.volume("2026-10") == 0


def test_estimates_are_readonly_and_execution_commit_is_idempotent():
    fees = model()
    ctx = context("same")
    quote = fees.quote(ctx, 2.4, 150)
    for _ in range(4):
        assert fees.quote(ctx, 2.4, 150) == quote
        assert deepcopy(fees).estimate(ctx, 2.4, 150).broker_commission == 0.35
    assert not fees.records and not fees.monthly_volumes
    record = fees.commit("execution", quote)
    assert fees.commit("execution", quote) == record
    assert len(fees.records) == 1 and fees.volume("2026-10") == 2.4
    with pytest.raises(ValueError, match="Stale"):
        fees.commit("different-execution", quote)


def test_new_day_and_amendment_restart_minimum_but_not_every_fractional_tail():
    fees = model()
    ctx = context("same", "2026-09-15")
    filled(fees, "first", 2, 100, ctx)
    filled(fees, "tail", 0.3, 100, ctx)
    filled(fees, "replacement", 0.2, 100, replace(ctx, generation=1))
    filled(fees, "next-day", 0.2, 100, context("same", "2026-09-16"))
    assert [r.fees.broker_commission for r in fees.records] == [0.35, 0, 0.2, 0.2]


def test_default_published_snapshot_remains_separate_and_drip_is_not_inferred():
    old = filled(IBKRProTieredUSStock(), "old", 2.4, 250).fees
    new = filled(model(), "new", 2.4, 250).fees
    assert old.broker_commission == pytest.approx(0.35 + 1)
    assert new.broker_commission == pytest.approx(0.35)
    assert new.total_fees > 0
    with pytest.raises(ValueError, match="reinvestment"):
        filled(model(), "drip", 0.02, 250, context("drip", venue="IBDRIPUS"))
    assert model().fractional_policy == "parent_order_tiered"
