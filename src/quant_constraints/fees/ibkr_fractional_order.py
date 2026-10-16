"""Isolated account-observed parent-order fee scenario; published model stays sealed."""

import math

from ..calendar import NY
from .ibkr_tiered import (
    SOURCE,
    FeeBreakdown,
    FeeExecutionContext,
    FeeQuote,
    IBKRProTieredUSStock,
    _OrderCosts,
    finite_nonnegative,
)


class IBKRParentOrderFractionalResearch(IBKRProTieredUSStock):
    """Research only: one cumulative tiered minimum/cap across whole and fractional fills.

    Internalized fractional venue cost is modeled as zero, NOT total commission.
    Clearing, regulatory and broker pass-through charges remain. DRIP is excluded.
    The inherited published model and all strict entry/PIT gates are untouched.
    Account observations do not establish a universal tariff or full historical bills.
    """

    fractional_policy = "parent_order_tiered"

    def quote(
        self, context: FeeExecutionContext, quantity: float, price: float, *, conservative=False
    ) -> FeeQuote:
        finite_nonnegative(quantity, "quantity")
        finite_nonnegative(price, "price")
        if quantity == 0 or price == 0:
            raise ValueError("Fees require positive filled quantity and price")
        if self._last_time is not None and context.timestamp < self._last_time:
            raise ValueError("Fee executions must be chronological")
        day = context.timestamp.astimezone(NY).date()
        proxy = self.history_mode == "current_snapshot_backcast"
        regime = (
            self.backcast_regulatory_rate
            if proxy
            else next((r for r in self.regulatory_rates if r.start <= day <= r.end), None)
        )
        if regime is None:
            raise ValueError(f"No explicit regulatory fee version for {day}")
        volume = self.volume(context.month)
        old = _OrderCosts() if conservative else self._orders.get(context.bucket, _OrderCosts())
        whole = float(math.floor(quantity))
        fractional = quantity - whole
        parent_tiered = self.fractional_policy == "parent_order_tiered"
        if parent_tiered and context.venue == "IBDRIPUS":
            raise ValueError(
                "Parent-order profile does not model dividend reinvestment commissions"
            )
        billed_quantity = quantity if parent_tiered else whole
        raw = old.raw + self.marginal(billed_quantity, 0 if conservative else volume)
        whole_value = old.whole_value + billed_quantity * price
        gross = max(raw, 0.35) if old.whole_quantity + billed_quantity > 0 else 0.0
        capped = gross > 0.01 * whole_value and whole_value > 0
        cumulative = min(gross, 0.01 * whole_value)
        broker_fee = cumulative - old.broker_whole
        if fractional > 1e-12 and not parent_tiered:
            broker_fee += max(0.01, 0.01 * fractional * price)
        counted = 0.0 if capped else old.whole_quantity + billed_quantity
        delta = counted - old.counted_whole + (0.0 if parent_tiered else fractional)
        costs = _OrderCosts(
            raw, whole_value, old.whole_quantity + billed_quantity, cumulative, counted
        )
        assumptions = [
            "single-account; absent external US/CA Tiered volume assumed zero",
            "whole/fractional components split per execution; no cent rounding",
            "online cap eligibility; capped whole components excluded",
            "CAT uses 2026-10-10 snapshot, not historical CAT archive",
            "published broker/venue snapshot 2026-10-10; taxes excluded",
        ]
        if proxy:
            assumptions.append(
                "counterfactual uniform snapshot backcast; SEC/TAF/CAT/venue proxies, "
                "not historical broker statements; normal nonholiday TAF scenario"
            )
        else:
            assumptions.append("strict dated regulatory coverage; broker/venue/CAT remain proxies")
        venue_quantity = whole if parent_tiered else quantity
        if parent_tiered:
            assumptions[1:3] = [
                "venue components split per execution; broker fees aggregate per parent order",
                "online cap eligibility; capped parent-order volume excluded",
            ]
            assumptions.extend(
                (
                    "fractional_policy=parent_order_tiered; explicit account-observed research scenario",
                    "one cumulative broker minimum/cap across whole and fractional fills per order/day/generation",
                    "fractional executions assumed internalized; external venue proxy only on whole components",
                    "not a universal published tariff, DRIP model, or complete account commission reconciliation",
                )
            )
        # Only displayed regular-hours NASDAQ/ARCA/IEX schedules are modeled.
        # Unknown routing is an explicit scenario, not a universal upper bound.
        if context.venue in {"NASDAQ", "ARCA", "IEX"} and context.liquidity != "unknown":
            if context.liquidity == "add":
                exchange = 0.0
                assumptions.append("no rebate credit, even for known adding-liquidity fills")
            elif context.venue == "IEX" and price < 1:
                exchange = venue_quantity * price * 0.002
            else:
                exchange = venue_quantity * (0.003 if price >= 1 else price * 0.003)
            venue_source = {"NASDAQ": "INET", "ARCA": "ARCA", "IEX": "IEX"}[context.venue]
            venue_source = (
                f"https://www.interactivebrokers.com/en/accounts/fees/{venue_source}stkfee.php"
            )
            assumptions.append("displayed regular-hours execution schedule assumed")
        else:
            exchange = venue_quantity * (
                self.unknown_venue_per_share if price >= 1 else price * self.unknown_venue_rate
            )
            venue_source = "https://www.interactivebrokers.com/en/accounts/fees/ARCAstkfee.php"
            assumptions.append(
                "unknown venue/liquidity: conservative ARCA-routed scenario; no rebate"
            )
        clearing = min(quantity * 0.0002, quantity * price * 0.005)
        sec = quantity * price * regime.sec_per_dollar if context.side == "SELL" else 0.0
        taf = (
            min(quantity * regime.taf_per_share, regime.taf_cap) if context.side == "SELL" else 0.0
        )
        cat = quantity * regime.cat_per_share
        passthrough = broker_fee * (0.000175 + 0.00056)
        fees = FeeBreakdown(
            broker_fee,
            exchange,
            clearing,
            sec + taf + cat,
            passthrough,
            sec,
            taf,
            cat,
            volume,
            delta,
            capped,
            regime.version,
            tuple(assumptions),
            (SOURCE, venue_source, *regime.sources),
            proxy,
            self.snapshot_date,
            self.history_mode,
        )
        if fees.total_fees < 0 or not math.isfinite(fees.total_fees):
            raise ValueError("Invalid total fees")
        return FeeQuote(context, quantity, price, fees, self._revision, costs)
