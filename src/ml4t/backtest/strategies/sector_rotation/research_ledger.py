"""Read-only Decimal reference ledger, independent of production accounting.

It consumes retained input terms and executed fills, never production receivable,
cash, settlement or fee-calculation helpers. Input settlement dates are an explicit
independent reference. The fee oracle covers the disclosed 1.0/0.1 share-step snapshot
scenario, not an assertion of historical IBKR bills.
"""

from collections import defaultdict
from datetime import date
from decimal import ROUND_FLOOR, Decimal, localcontext

from quant_constraints.calendar import NY

from .research_inputs import ResearchInputs, number

TOLERANCE = Decimal("1E-8")


class ResearchLedgerMismatch(ValueError):
    pass


def _equal(actual, expected, field):
    if abs(number(actual) - expected) > TOLERANCE:
        raise ResearchLedgerMismatch(f"Independent research ledger mismatch: {field}")


def _broker_charge(quantity, volume):
    """Integrate marginal per-share rates over the filled quantity interval."""
    result = Decimal(0)
    lower = Decimal(0)
    for upper, rate in (
        (Decimal(300000), Decimal("0.0035")),
        (Decimal(3000000), Decimal("0.002")),
        (Decimal(20000000), Decimal("0.0015")),
        (Decimal(100000000), Decimal("0.001")),
        (Decimal("Infinity"), Decimal("0.0005")),
    ):
        overlap = max(Decimal(0), min(volume + quantity, upper) - max(volume, lower))
        result += overlap * rate
        lower = upper
    return result


def _on_step(quantity: Decimal, step: Decimal) -> bool:
    units = quantity / step
    return abs(units - units.to_integral_value()) <= Decimal("1E-9")


class _FeeReference:
    """Unknown-venue, normal-charge 2026 snapshot backcast; no rebates."""

    def __init__(self, share_step: Decimal = Decimal("1")):
        self.share_step = number(share_step)
        self.months = defaultdict(Decimal)
        self.orders = {}

    def charge(self, fill, generation):
        quantity, price = number(fill.quantity), number(fill.price)
        if not _on_step(quantity, self.share_step) or price < 1:
            raise ResearchLedgerMismatch(
                "Reference fee scope requires exact supported share steps and price >=1"
            )
        day = fill.timestamp.astimezone(NY).date()
        month = day.strftime("%Y-%m")
        key = fill.order_id, generation, day
        prior_raw, prior_value, prior_paid, prior_quantity, prior_counted = self.orders.get(
            key, (Decimal(0),) * 5
        )
        whole = quantity.to_integral_value(rounding=ROUND_FLOOR)
        fractional = quantity - whole
        parent_tiered = self.share_step == Decimal("0.1")
        billed = quantity if parent_tiered else whole
        raw = prior_raw + _broker_charge(billed, self.months[month])
        value = prior_value + billed * price
        gross = max(Decimal("0.35"), raw) if prior_quantity + billed > 0 else Decimal(0)
        capped = gross > value / 100 and value > 0
        cumulative = min(gross, value / 100)
        broker = cumulative - prior_paid
        if fractional > Decimal("1E-12") and not parent_tiered:
            broker += max(Decimal("0.01"), fractional * price / 100)
        counted = Decimal(0) if capped else prior_quantity + billed
        self.months[month] += counted - prior_counted + (0 if parent_tiered else fractional)
        self.orders[key] = raw, value, cumulative, prior_quantity + billed, counted
        venue = (whole if parent_tiered else quantity) * Decimal("0.0035")
        clearing = min(quantity * Decimal("0.0002"), quantity * price * Decimal("0.005"))
        regulatory = quantity * Decimal("0.000003")
        if fill.side.value == "sell":
            regulatory += quantity * price * Decimal("0.0000206")
            regulatory += min(quantity * Decimal("0.000195"), Decimal("9.79"))
        return broker * Decimal("1.000735") + venue + clearing + regulatory


def verify_research_ledger(inputs: ResearchInputs, assumptions, fills, orders, fee_records, trace):
    """Check every observed bar; fail before returning a successful research result."""
    if not trace:
        raise ResearchLedgerMismatch("No Engine observations to verify")
    expected_stamps = sorted({bar.end for bar in inputs.bars})
    if [state["timestamp"] for state in trace] != expected_stamps:
        raise ResearchLedgerMismatch("Incomplete or unordered Engine observation coverage")
    if [fill.timestamp for fill in fills] != sorted(fill.timestamp for fill in fills):
        raise ResearchLedgerMismatch("Unordered executed fills")
    cash = number(assumptions.initial_cash)
    positions = defaultdict(Decimal)
    receivables = {}
    holds = []
    events = []
    first_day = trace[0]["timestamp"].astimezone(NY).date()
    for event in inputs.splits:
        if event.session >= first_day:
            events.append((event.session, 0, event))
    for event in inputs.distributions:
        if event.ex_date >= first_day:
            events.append((event.ex_date, 1, event))
            events.append((event.pay_date, 2, event))
    events.sort(key=lambda item: (item[0], item[1], item[2].asset, item[2].event_id))
    bar_map = {(b.end, b.asset): b for b in inputs.bars}
    order_map = {o.order_id: o for o in orders}
    if len(order_map) != len(orders):
        raise ResearchLedgerMismatch("Duplicate order identity")
    if len(fee_records) != len(fills):
        raise ResearchLedgerMismatch("Every fill needs one committed fee record")
    if len({r.execution_id for r in fee_records}) != len(fee_records):
        raise ResearchLedgerMismatch("Duplicate committed fee execution identity")
    fill_index = event_index = 0
    participation = defaultdict(Decimal)
    step = number(assumptions.share_step)
    fee_reference = _FeeReference(step)
    checkpoints = 0
    max_error = Decimal(0)
    with localcontext() as context:
        context.prec = 60
        for state in trace:
            stamp = state["timestamp"]
            day = stamp.astimezone(NY).date()
            holds = [(d, amount) for d, amount in holds if d > day]
            while event_index < len(events) and events[event_index][0] <= day:
                _, kind, event = events[event_index]
                key = event.asset, event.event_id
                if kind == 0:
                    quantity = positions[event.asset] * number(event.new_per_old)
                    if not _on_step(quantity, step) or (
                        number(event.new_per_old) < 1 and quantity != quantity.to_integral_value()
                    ):
                        raise ResearchLedgerMismatch(
                            "UNSUPPORTED_CASH_IN_LIEU: fractional split result"
                        )
                    positions[event.asset] = quantity
                elif kind == 1:
                    receivables[key] = (
                        positions[event.asset]
                        * number(event.cash_amount)
                        * (1 - number(assumptions.dividend_tax_rate))
                    )
                else:
                    cash += receivables.pop(key, Decimal(0))
                event_index += 1
            while fill_index < len(fills) and fills[fill_index].timestamp <= stamp:
                fill = fills[fill_index]
                bar = bar_map.get((fill.timestamp, fill.asset))
                order = order_map.get(fill.order_id)
                if order is None or order.asset != fill.asset or order.side != fill.side:
                    raise ResearchLedgerMismatch("Fill/order identity mismatch")
                if (
                    bar is None
                    or order.created_at is None
                    or order.created_at >= fill.timestamp
                    or not 9 * 60 + 35
                    <= fill.timestamp.astimezone(NY).hour * 60
                    + fill.timestamp.astimezone(NY).minute
                    < 9 * 60 + 45
                ):
                    raise ResearchLedgerMismatch("Noncausal or outside-window execution")
                quantity = number(fill.quantity)
                if quantity <= 0 or not _on_step(quantity, step):
                    raise ResearchLedgerMismatch("Fill quantity violates research share step")
                participation[fill.timestamp, fill.asset] += quantity
                if participation[fill.timestamp, fill.asset] > number(bar.volume) * number(
                    assumptions.maximum_participation
                ):
                    raise ResearchLedgerMismatch("Execution exceeds disclosed minute participation")
                buy = fill.side.value == "buy"
                slip = number(bar.close) * number(assumptions.slippage_bps) / 10000
                price = number(bar.close) + (slip if buy else -slip)
                _equal(fill.price, price, "next_completed_minute_close_plus_slippage")
                _equal(fill.slippage, slip, "slippage")
                record = fee_records[fill_index]
                if (
                    record.context.order_id != fill.order_id
                    or record.context.timestamp != fill.timestamp
                    or record.context.asset != fill.asset
                    or record.context.side != fill.side.value.upper()
                    or number(record.quantity) != quantity
                    or number(record.price) != number(fill.price)
                ):
                    raise ResearchLedgerMismatch("Fee record/fill identity mismatch")
                fee = fee_reference.charge(fill, record.context.generation)
                _equal(fill.commission, fee, "snapshot_tiered_fee")
                _equal(record.fees.total_fees, fee, "committed_fee_record")
                amount = quantity * price
                if buy:
                    settled = cash - sum((v for _, v in holds), Decimal(0))
                    if amount + fee > settled + TOLERANCE:
                        raise ResearchLedgerMismatch("Purchase reused unsettled sale proceeds")
                    cash -= amount + fee
                    positions[fill.asset] += quantity
                else:
                    if quantity > positions[fill.asset]:
                        raise ResearchLedgerMismatch("Sale exceeds owned shares")
                    positions[fill.asset] -= quantity
                    cash += amount - fee
                    delay = 1 if day >= date(2024, 5, 28) else 2 if day >= date(2017, 9, 5) else 3
                    future = [d for d in inputs.settlement_sessions if d > day]
                    if len(future) < delay:
                        raise ResearchLedgerMismatch("Independent settlement coverage exhausted")
                    holds.append((future[delay - 1], amount - fee))
                fill_index += 1
            active = {a: q for a, q in positions.items() if q}
            actual_positions = {a: number(q) for a, q in state["positions"].items() if number(q)}
            if (
                set(active) != set(actual_positions)
                or any(
                    abs(q - actual_positions[asset])
                    > (Decimal(0) if step == 1 else Decimal("1E-9"))
                    for asset, q in active.items()
                )
                or any(not _on_step(q, step) for q in active.values())
            ):
                raise ResearchLedgerMismatch("Independent quantity ledger mismatch")
            accrued = sum(receivables.values(), Decimal(0))
            settled = cash - sum((v for _, v in holds), Decimal(0))
            value = (
                cash
                + accrued
                + sum((q * number(bar_map[stamp, a].close) for a, q in active.items()), Decimal(0))
            )
            for field, expected in (
                ("cash", cash),
                ("settled_cash", settled),
                ("receivables", accrued),
                ("NAV", value),
            ):
                error = abs(number(state[field]) - expected)
                max_error = max(max_error, error)
                _equal(state[field], expected, field)
                checkpoints += 1
        if fill_index != len(fills):
            raise ResearchLedgerMismatch("Unobserved fills after final ledger checkpoint")
    return {
        "status": "RETROSPECTIVE_ENGINE_LEDGER_PASS",
        "monetary_checkpoints": checkpoints,
        "quantity_checkpoints": len(trace),
        "observation_coverage": "exact_input_timestamps",
        "fills_checked": len(fills),
        "maximum_monetary_error": str(max_error),
        "money_tolerance": str(TOLERANCE),
        "quantity_tolerance": "exact" if step == 1 else "1E-9_float_representation_only",
        "historical_PIT_verified": False,
        "actual_broker_execution_verified": False,
        "actual_broker_credit_verified": False,
    }
