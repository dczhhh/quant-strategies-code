"""Private current-snapshot mechanics, never a historical-PIT or execution admission.

Real captured prices/terms are distinct from controlled positions/orders. This
operator invokes the pinned production split component directly; it neither runs
Engine nor passes invented intraday bars or PIT coverage to the real entry point.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import runpy
import subprocess
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

s = SimpleNamespace(**runpy.run_path(str(Path(__file__).with_name("real_case_supplement.py"))))
b, m = s.b, s.m
NY = ZoneInfo("America/New_York")
RUNTIME_SHA = "d2115c9780e9613b84e44939e25a7e7a2fc4a8cc"
CAPTURE_SHA = "c383bdab9661f9ebd2f18a5bb776194d871d64b0"
INPUT_COMMITS = {
    "sqqq-reverse-identity-pre": "6bb9c6a038afc0d04b38751b654f32a92a86a379",
    "sqqq-reverse-issuer-8937": "d4a416aa9b134a4fde487c8691cf3d7f20084850",
    "sqqq-reverse-issuer-announcement": "fe8bfe4f183604f99f6ca3beb06e7eb699b95267",
    "sqqq-reverse-raw": "64527639532b0ecd523cea30bdb194b95b3aa8a7",
    "sqqq-reverse-split-adjusted": "b8350f8a90effe4b9999442c5c15022c36874391",
    "sqqq-reverse-splits": "bc46b24044435bb5ea6212aabe2300c3b45a698e",
    "wolf-reorganization-identity-post": "fe49bb42bfffbd45840cbb652c826455c7df49f9",
    "wolf-reorganization-identity-pre": "4492087a60ee4f9af459afdeb4b0db66b463eaf9",
    "wolf-reorganization-issuer-2026-release": "6b7b79095bb5f255b3766392b4ace84a1c8d0877",
    "wolf-reorganization-issuer-reorganization": "db9ea3a57f30835b5240e333ef602e85fec37b97",
    "wolf-reorganization-raw": "60aec84b8d20794187fd12c77c273aaec462a47d",
    "wolf-reorganization-split-adjusted": "dd21010452a58c4a7f31e57e6aa5a58188eaf6a0",
    "wolf-reorganization-splits": "287348015bc893f6526b3fd81e711a9cc423d035",
}
ANNOUNCEMENT_SHA256 = "63e11d1ac11ee66533c38f50ac76ac17baea56e33c0dfb0cdeb0cf481534c448"
FORM_SHA256 = "1257b46dc21a856d1b8a53359cbd8a218ece5f6e0ac87c23c32ee79b32d806c3"
PREFIX = "research/verification/real_split_probe_v1/"
CASE_ID = "SQQQ-2024-11-07-current-snapshot-component"
EVENT_DAY = date(2024, 11, 7)
PRE_DAY = date(2024, 11, 6)
RATIO = Decimal(1) / Decimal(5)  # Independent ProShares terms, not processor output.
SOURCE_PINS = {
    "config.py": "85c6b0fa6712208c6e210defa5aa872a6527d9cc5a1f9645c7427895abae7e67",
    "dividend_tax.py": "822e144f2e35c26f57d38234d55b55f28a47307d6afac004a25dc9d047ce3991",
    "corporate_actions/events.py": "84f9a54dc6c6252bb4661d65773be03d920ce3aad1d43398a605391145c2ae42",
    "corporate_actions/processor.py": "36cb7e98c52efb9629d98d0a73ef884a347fbbb21c22cb26dff9d3c3983d8e3e",
}
LIMITS = {
    "quantity": "0.000000000001",
    "money_usd": "0.00000001",
    "adjusted_price_relative": "0.000002",
    "adjusted_price_absolute": "0.0001",
}


class Blocked(ValueError):
    pass


def need(condition: bool, code: str) -> None:
    if not condition:
        raise Blocked(code)


def decimal(value, *, positive=True) -> Decimal:
    need(type(value) in (str, int, float, Decimal), "INVALID_NUMBER")
    try:
        number = Decimal(str(value))
    except Exception as error:
        raise Blocked("INVALID_NUMBER") from error
    need(number.is_finite() and (number > 0 if positive else number >= 0), "INVALID_NUMBER")
    return number


def decoded(body: bytes) -> dict:
    try:
        result = json.loads(body, parse_float=Decimal, object_pairs_hook=b.pairs)
    except Exception as error:
        raise Blocked("INVALID_JSON") from error
    need(type(result) is dict and result.get("status") in {"OK", "DELAYED"}, "INVALID_STATUS")
    need(not result.get("next_url"), "PAGINATION_UNVERIFIED")
    return result


def bars(body: bytes, adjusted: bool) -> dict[date, dict]:
    data = decoded(body)
    need(data.get("ticker") == "SQQQ" and data.get("adjusted") is adjusted, "PRODUCT_MISMATCH")
    rows = data.get("results")
    if not isinstance(rows, list) or not rows:
        raise Blocked("EMPTY_BARS")
    output = {}
    previous = None
    for row in rows:
        need(type(row) is dict and type(row.get("t")) is int, "INVALID_BAR")
        stamp = datetime.fromtimestamp(row["t"] / 1000, UTC).astimezone(NY)
        day = stamp.date()
        need(stamp.hour == stamp.minute == stamp.second == 0, "DAILY_BUCKET_UNVERIFIED")
        need(date(2024, 11, 4) <= day <= date(2024, 11, 12), "BAR_WINDOW_MISMATCH")
        need(previous is None or day > previous, "BAR_ORDER_MISMATCH")
        prices = {key: decimal(row.get(key)) for key in ("o", "h", "l", "c")}
        need(
            prices["l"] <= min(prices["o"], prices["c"])
            and prices["h"] >= max(prices["o"], prices["c"]),
            "INVALID_OHLC",
        )
        output[day] = {**prices, "v": decimal(row.get("v"), positive=False)}
        previous = day
    need(PRE_DAY in output and EVENT_DAY in output, "EVENT_BOUNDARY_MISSING")
    return output


def price_factors(body: bytes) -> list[tuple[date, Decimal]]:
    rows = decoded(body).get("results")
    if not isinstance(rows, list) or not rows:
        raise Blocked("EMPTY_SPLITS")
    factors = []
    previous = None
    for row in rows:
        need(type(row) is dict and row.get("ticker") == "SQQQ", "SPLIT_PRODUCT_MISMATCH")
        day = date.fromisoformat(row["execution_date"])
        need(date(2024, 11, 4) <= day <= date(2026, 10, 14), "SPLIT_WINDOW_MISMATCH")
        need(previous is None or day > previous, "SPLIT_ORDER_MISMATCH")
        ratio = decimal(row.get("split_to")) / decimal(row.get("split_from"))
        need(row.get("adjustment_type") in {"forward_split", "reverse_split"}, "SPECIAL_ACTION")
        need((ratio < 1) == (row["adjustment_type"] == "reverse_split"), "SPLIT_KIND_MISMATCH")
        if day == EVENT_DAY:
            need(ratio == RATIO, "ISSUER_FACTOR_MISMATCH")
        factors.append((day, decimal(row.get("historical_adjustment_factor"))))
        previous = day
    need(any(day == EVENT_DAY for day, _ in factors), "ISSUER_EVENT_MISSING")
    return factors


def adjustment_check(raw: dict, adjusted: dict, factors: list) -> dict:
    need(set(raw) == set(adjusted), "RAW_ADJUSTED_TIMELINE_MISMATCH")
    relative_limit = Decimal(LIMITS["adjusted_price_relative"])
    absolute_limit = Decimal(LIMITS["adjusted_price_absolute"])
    maximum = Decimal(0)
    for day, row in raw.items():
        # Provider-documented cumulative factor of the first later action. Do not
        # assume the event-window closing day is the current adjustment anchor.
        factor = next((value for effective, value in factors if effective > day), Decimal(1))
        for field in ("o", "h", "l", "c"):
            expected = row[field] * factor
            difference = abs(adjusted[day][field] - expected)
            need(
                difference <= max(absolute_limit, expected * relative_limit), "ADJUSTMENT_MISMATCH"
            )
            maximum = max(maximum, difference)
    pre = next(value for day, value in factors if day > PRE_DAY)
    post = next((value for day, value in factors if day > EVENT_DAY), Decimal(1))
    need(abs(pre / post - 1 / RATIO) <= relative_limit, "FACTOR_BOUNDARY_MISMATCH")
    return {"bar_count": len(raw), "max_price_residual": str(maximum), "mode": "split_only"}


def expected_values(pre_price: Decimal, mark: Decimal, *, after: bool) -> dict:
    quantity = Decimal(50) * (RATIO if after else 1)
    entry = pre_price / (RATIO if after else 1)
    cash = Decimal(100000) - Decimal(50) * pre_price
    return {
        "quantity": quantity,
        "entry_price": entry,
        "basis": Decimal(50) * pre_price,
        "cash": cash,
        "nav": cash + quantity * mark,
        "unrealized_pnl": quantity * (mark - entry),
    }


def runtime_checked() -> None:
    import quant_constraints

    package = Path(quant_constraints.__file__).resolve().parent
    for relative, pin in SOURCE_PINS.items():
        need(
            hashlib.sha256((package / relative).read_bytes()).hexdigest() == pin,
            "RUNTIME_PIN_MISMATCH",
        )


def controlled_broker(pre_price: Decimal, observed: datetime):
    from ml4t.backtest.config import CommissionType, DataFrequency, SlippageType
    from ml4t.backtest.risk import StopLoss
    from ml4t.backtest.risk.position.composite import RuleChain
    from ml4t.backtest.risk.position.dynamic import VolatilityStop
    from ml4t.backtest.types import Order, OrderSide, OrderStatus, OrderType, Position
    from quant_constraints import (
        ConstraintConfig,
        ConstraintController,
        DividendTaxRule,
        InMemoryEarningsProvider,
        MarketContext,
        broker_factory,
        cash_backtest_config,
    )
    from quant_constraints.corporate_actions import (
        CorporateAction,
        CorporateActionCoverage,
        InMemoryCorporateActionProvider,
    )

    sid = "issuer-cusip-transition:74347G432/74347G192"
    event = CorporateAction(
        sid,
        CASE_ID,
        "SPLIT",
        EVENT_DAY,
        observed,
        "ProShares-announcement-sha256:" + ANNOUNCEMENT_SHA256,
        split_ratio=float(RATIO),
    )
    coverage = CorporateActionCoverage(
        sid,
        observed,
        date(2024, 11, 4),
        observed.astimezone(NY).date(),
        "current-capture-not-historical-PIT",
        point_in_time=False,
    )
    provider = InMemoryCorporateActionProvider((event,), (coverage,))
    settings = ConstraintConfig(
        corporate_actions_enabled=True,
        dividend_tax_profile="explicit_rules",
        dividend_tax_scenario="controlled-split-only-no-tax-calculation",
        dividend_tax_rules=(
            DividendTaxRule(
                "ZZ",
                0.0,
                date(2024, 1, 1),
                date(2027, 1, 1),
                observed,
                observed,
                "invented-unapplied-tax-fixture",
                "fixture-v1",
            ),
        ),
        pricing_plan="custom",
        slippage_mode="configured",
        drift_reduction="disabled",
    )
    controller = ConstraintController(settings, InMemoryEarningsProvider())

    def context(stamp, asset, phase):
        need(asset == "SQQQ", "UNEXPECTED_ASSET")
        return MarketContext(
            stamp,
            float(pre_price),
            phase=phase,
            data_frequency="1d",
            instrument="unknown",
            security_id=sid,
            execution_data_mode="raw_execution",
            risk_data_mode="raw_execution",
            signal_data_mode="split_adjusted_signal",
        )

    config = cash_backtest_config(
        initial_cash=100000,
        data_frequency=DataFrequency.IRREGULAR,
        commission_type=CommissionType.NONE,
        commission_per_share=0,
        commission_minimum=0,
        slippage_type=SlippageType.NONE,
        slippage_rate=0,
    )
    broker = broker_factory(controller, context, corporate_action_provider=provider)(config)
    expected = expected_values(pre_price, pre_price, after=False)
    broker.account.cash = broker.account._lock_notional_free_cash = float(expected["cash"])
    entry_at = datetime(2024, 11, 6, 16, tzinfo=NY)
    broker.positions["SQQQ"] = Position(
        "SQQQ",
        50,
        float(pre_price),
        entry_at,
        current_price=float(pre_price),
        high_water_mark=float(pre_price * Decimal("1.1")),
        low_water_mark=float(pre_price * Decimal("0.9")),
        entry_commission=0,
    )
    broker.positions["SQQQ"].context["atr"] = float(pre_price * Decimal("0.02"))
    broker._risk_state.position_rules = RuleChain(
        [
            StopLoss(0.1),
            VolatilityStop(_entry_atr=float(pre_price * Decimal("0.02"))),
        ]
    )
    broker._risk_state.pending_exits["SQQQ"] = {
        "quantity": 5,
        "fill_price": float(pre_price * Decimal("0.95")),
    }
    # Controlled canonical orders are transformation probes, never submitted
    # trading intents for this leveraged/inverse ETF or inferred actual orders.
    parent = Order(
        "SQQQ",
        OrderSide.BUY,
        50,
        order_id="probe-parent",
        status=OrderStatus.FILLED,
        filled_quantity=50,
        filled_price=float(pre_price),
    )
    target = Order(
        "SQQQ",
        OrderSide.SELL,
        50,
        OrderType.LIMIT,
        limit_price=float(pre_price * Decimal("1.2")),
        parent_id=parent.order_id,
        order_id="probe-target",
    )
    stop = Order(
        "SQQQ",
        OrderSide.SELL,
        50,
        OrderType.STOP,
        stop_price=float(pre_price * Decimal("0.9")),
        parent_id=parent.order_id,
        order_id="probe-stop",
    )
    pending = Order(
        "SQQQ",
        OrderSide.BUY,
        10,
        OrderType.LIMIT,
        limit_price=float(pre_price * Decimal("0.8")),
        order_id="probe-buy",
        _reservation_price=float(pre_price * Decimal("0.8")),
        _reserved_cash=float(Decimal(10) * pre_price * Decimal("0.8")),
    )
    broker._order_state.orders.extend((parent, target, stop, pending))
    broker._order_state.pending.extend((target, stop, pending))
    broker.bracket_children[parent.order_id] = (target.order_id, stop.order_id)
    broker._market_state.time = observed
    return broker, event


def state(broker) -> dict:
    return copy.deepcopy(
        {
            "positions": broker.positions,
            "orders": broker.orders,
            "cash": broker.cash,
            "settled": broker.settled_cash,
            "unsettled": broker.unsettled_cash,
            "receivables": broker.account._receivables,
            "reserved": broker.reserved_cash,
            "fills": broker.fills,
            "trades": broker.trades,
            "ca": broker.corporate_action_processor.state,
            "risk": broker._risk_state,
        }
    )


def rejected_without_mutation(broker, action, prefix: str) -> bool:
    before = state(broker)
    try:
        action()
    except ValueError as error:
        need(str(error).startswith(prefix), "UNEXPECTED_REJECTION")
    else:
        raise Blocked("MISSING_REJECTION")
    need(state(broker) == before, "REJECTION_MUTATED_STATE")
    return True


def mechanical_probe(raw: dict, observed: datetime) -> dict:
    runtime_checked()
    pre_price = raw[PRE_DAY]["c"]
    broker, event = controlled_broker(pre_price, observed)
    processor = broker.corporate_action_processor
    pit_rejected = rejected_without_mutation(
        broker,
        lambda: processor.prepare(observed, {"SQQQ"}),
        "Missing point-in-time corporate action coverage:",
    )
    journal = []
    maxima = {key: Decimal(0) for key in ("quantity", "basis", "cash", "nav", "unrealized_pnl")}
    fixed = (broker.cash, broker.settled_cash, broker.unsettled_cash, broker.reserved_cash)
    for day, row in raw.items():
        if day < PRE_DAY:
            continue  # The controlled checkpoint is seeded at the actual pre-event close.
        if day == EVENT_DAY:
            # Direct isolated component call with the actual capture clock;
            # no prepare/apply tuple fabricated as historical PIT evidence.
            before_nav = broker.account.total_equity
            processor.validate_terms(event)
            processor.split("SQQQ", event, {"SQQQ"})
            need(
                abs(Decimal(str(broker.account.total_equity)) - Decimal(str(before_nav)))
                <= Decimal(LIMITS["money_usd"]),
                "SPLIT_BOUNDARY_NAV_MISMATCH",
            )
        broker.account.mark_to_market({"SQQQ": float(row["c"])})
        expected = expected_values(pre_price, row["c"], after=day >= EVENT_DAY)
        pos = broker.positions["SQQQ"]
        actual = {
            "quantity": Decimal(str(pos.quantity)),
            "basis": Decimal(str(pos.quantity)) * Decimal(str(pos.entry_price)),
            "cash": Decimal(str(broker.cash)),
            "nav": Decimal(str(broker.account.total_equity)),
            "unrealized_pnl": Decimal(str(pos.unrealized_pnl())),
        }
        errors = {key: abs(actual[key] - expected[key]) for key in actual}
        for key, error in errors.items():
            limit = Decimal(LIMITS["quantity"] if key == "quantity" else LIMITS["money_usd"])
            need(error <= limit, "MECHANICAL_EXPECTATION_MISMATCH")
            maxima[key] = max(maxima[key], error)
        journal.append(
            {
                "date": day.isoformat(),
                "raw_close": str(row["c"]),
                "expected": {key: str(value) for key, value in expected.items()},
                "actual": {key: str(value) for key, value in actual.items()},
                "absolute_errors": {key: str(value) for key, value in errors.items()},
            }
        )
    parent, target, stop, pending = broker.orders
    quantity_limit, money_limit = Decimal(LIMITS["quantity"]), Decimal(LIMITS["money_usd"])
    for actual, expected in (
        (parent.filled_quantity, Decimal(10)),
        (target.quantity, Decimal(10)),
        (stop.quantity, Decimal(10)),
        (pending.quantity, Decimal(2)),
    ):
        need(abs(Decimal(str(actual)) - expected) <= quantity_limit, "ORDER_QUANTITY_MISMATCH")
    for actual, expected in (
        (target.limit_price, pre_price * 6),
        (stop.stop_price, pre_price * Decimal("4.5")),
        (pending.limit_price, pre_price * 4),
    ):
        need(abs(Decimal(str(actual)) - expected) <= money_limit, "ORDER_PRICE_MISMATCH")
    pos = broker.positions["SQQQ"]
    scaled_rule = broker._risk_state.position_rules_by_asset["SQQQ"].rules[1]
    for actual, expected in (
        (pos.high_water_mark, pre_price * Decimal("5.5")),
        (pos.low_water_mark, pre_price * Decimal("4.5")),
        (pos.context["atr"], pre_price * Decimal("0.1")),
        (scaled_rule._entry_atr, pre_price * Decimal("0.1")),
        (broker._risk_state.pending_exits["SQQQ"]["quantity"], Decimal(1)),
        (broker._risk_state.pending_exits["SQQQ"]["fill_price"], pre_price * Decimal("4.75")),
    ):
        need(abs(Decimal(str(actual)) - expected) <= money_limit, "RISK_UNIT_MISMATCH")
    need(
        all(
            abs(a - z) <= float(money_limit)
            for a, z in zip(
                (broker.cash, broker.settled_cash, broker.unsettled_cash, broker.reserved_cash),
                fixed,
                strict=True,
            )
        ),
        "CASH_OR_RESERVATION_CHANGED",
    )
    need(not broker.fills and not broker.trades, "SYNTHETIC_TRADES_CREATED")
    fractional, fractional_event = controlled_broker(pre_price, observed)
    fractional.positions["SQQQ"].quantity = 53
    fractional_rejected = rejected_without_mutation(
        fractional,
        lambda: fractional.corporate_action_processor.split("SQQQ", fractional_event, {"SQQQ"}),
        "UNSUPPORTED_CASH_IN_LIEU:",
    )
    from dataclasses import replace

    reorganization_rejected = rejected_without_mutation(
        broker,
        lambda: processor.validate_terms(replace(event, kind="REORGANIZATION")),
        "Unsupported exposed corporate action:",
    )
    return {
        "case_id": CASE_ID,
        "scope": "isolated_production_component_with_real_daily_marks",
        "controlled_position_and_orders": True,
        "actual_broker_statement": False,
        "tax_and_fees": "controlled_unapplied_tax_fixture_zero_fees_not_evidence",
        "component_status": "PASS",
        "journal": journal,
        "max_absolute_errors": {key: str(value) for key, value in maxima.items()},
        "order_and_oco_units_pass": True,
        "risk_price_and_pending_exit_units_pass": True,
        "cash_and_reservations_unchanged": True,
        "fractional_cash_rejected": fractional_rejected,
        "reorganization_kind_rejected": reorganization_rejected,
        "production_PIT_preflight_rejected": pit_rejected,
        "observed_at": observed.isoformat(),
        "historical_known_at": None,
    }


class PinnedInputs:
    """Read captured commit graphs, never follow a mutable input branch again."""

    def __init__(self, destination):
        self.destination, self.token = destination, destination.token
        self.parents = {}

    def ref(self, name: str):
        for group in s.GROUPS:
            for product in s.products(group):
                revision = INPUT_COMMITS[product["id"]]
                if name == s.ref(product):
                    return revision
                if name == s.ref(product, True):
                    if revision not in self.parents:
                        commit = self.destination.request("GET", "/git/commits/" + revision)
                        need(
                            commit is not None and len(commit["parents"]) == 1,
                            "INPUT_PARENT_MISMATCH",
                        )
                        self.parents[revision] = m.sha(commit["parents"][0]["sha"])
                    return self.parents[revision]
        raise Blocked("UNREVIEWED_INPUT_REF")

    def request(self, method: str, suffix: str, payload=None):
        need(method == "GET" and payload is None, "INPUT_WRITE_FORBIDDEN")
        return self.destination.request(method, suffix)

    def blob(self, sha, size):
        return self.destination.blob(sha, size)


def inputs(destination) -> dict:
    destination = PinnedInputs(destination)
    output = {}
    for group in s.GROUPS:
        for product in s.products(group):
            check = s.verify_product(destination, product)
            prefix = s.PREFIX + product["id"] + "/"
            payload, _ = b.read(
                destination,
                s.ref(product),
                {
                    prefix + "response.bin",
                    prefix + "receipt.json",
                    prefix + "checks.json",
                },
            )
            receipt = b.decode(payload[prefix + "receipt.json"])
            need(receipt["source_commit"] == CAPTURE_SHA, "CAPTURE_SOURCE_MISMATCH")
            output[product["id"]] = {
                "body": payload[prefix + "response.bin"],
                "receipt": receipt,
                "check": check,
                "commit": destination.ref(s.ref(product)),
            }
    need(len(output) == 13, "INPUT_COUNT_MISMATCH")
    return output


def report_from_inputs(captured: dict) -> dict:
    for product in s.products("sqqq-reverse"):
        need(captured[product["id"]]["check"]["structure_accepted"], "SQQQ_INPUT_UNVERIFIED")
    announcement = captured["sqqq-reverse-issuer-announcement"]["body"]
    form = captured["sqqq-reverse-issuer-8937"]["body"]
    need(hashlib.sha256(announcement).hexdigest() == ANNOUNCEMENT_SHA256, "ISSUER_PIN_MISMATCH")
    need(hashlib.sha256(form).hexdigest() == FORM_SHA256, "ISSUER_PIN_MISMATCH")
    raw = bars(captured["sqqq-reverse-raw"]["body"], False)
    adjusted = bars(captured["sqqq-reverse-split-adjusted"]["body"], True)
    factors = price_factors(captured["sqqq-reverse-splits"]["body"])
    observed = max(
        datetime.fromisoformat(item["receipt"]["fetched_at"]) for item in captured.values()
    )
    need(observed.tzinfo is not None, "INVALID_CAPTURE_CLOCK")
    component = mechanical_probe(raw, observed)
    adjustment = adjustment_check(raw, adjusted, factors)
    pre_identity = decoded(captured["wolf-reorganization-identity-pre"]["body"]).get("results")
    post_identity = decoded(captured["wolf-reorganization-identity-post"]["body"]).get("results")
    if not isinstance(pre_identity, dict) or not isinstance(post_identity, dict):
        raise Blocked("WOLF_IDENTITY_UNVERIFIED")
    need(
        pre_identity.get("ticker") == post_identity.get("ticker") == "WOLF",
        "WOLF_IDENTITY_UNVERIFIED",
    )
    identity_fields = ("composite_figi", "share_class_figi", "cik")
    comparable = [
        field for field in identity_fields if pre_identity.get(field) and post_identity.get(field)
    ]
    wolf = {
        "case_id": "WOLF-2025-reorganization-and-2026-release",
        "status": "UNSUPPORTED_REORGANIZATION_UNVERIFIED",
        "native_unsupported_kind_rejection": component["reorganization_kind_rejected"],
        "native_guard_scope": "controlled_generic_kind_guard_not_complete_real_WOLF_case",
        "identity_change_observed": any(
            pre_identity[field] != post_identity[field] for field in comparable
        ),
        "identity_fields_compared": comparable,
        "split_structure_accepted": captured["wolf-reorganization-splits"]["check"][
            "structure_accepted"
        ],
        "SEC_http_status": captured["wolf-reorganization-issuer-reorganization"]["receipt"][
            "http_status"
        ],
        "issuer_2026_http_status": captured["wolf-reorganization-issuer-2026-release"]["receipt"][
            "http_status"
        ],
        "no_exchange_ratio_inferred": True,
    }
    return {
        "schema_version": "real_split_component_probe_v1",
        "runtime_commit": RUNTIME_SHA,
        "capture_commit": CAPTURE_SHA,
        "input_provenance": {
            key: {"archive_commit": item["commit"], "receipt": item["receipt"]}
            for key, item in captured.items()
        },
        "issuer_terms": {
            "first_post_split_session": EVENT_DAY.isoformat(),
            "new_old_ratio": str(RATIO),
            "old_cusip": "74347G432",
            "new_cusip": "74347G192",
        },
        "tolerances": LIMITS,
        "component": component,
        "adjustment": adjustment,
        "wolf": wolf,
        "certification": "UNVERIFIED",
        "full_real_scenario_accepted": False,
        "intraday_execution_replayed": False,
        "strategy_performance_computed": False,
        "isolated_component_broker_invoked": True,
        **b.FALSE_FLAGS,
    }


def public_summary(report: dict) -> dict:
    return {
        "status": "REAL_SPLIT_COMPONENT_PROBE_COMPLETE_NOT_CERTIFIED",
        "runtime_commit": RUNTIME_SHA,
        "vendor_requests": 0,
        "input_products": 13,
        "sqqq_component": report["component"]["component_status"],
        "real_daily_marks": len(report["component"]["journal"]),
        "raw_adjusted_bars": report["adjustment"]["bar_count"],
        "split_adjustment_check": "PASS",
        "order_and_oco_units": "PASS",
        "risk_and_pending_exit_units": "PASS",
        "fractional_cash": "UNSUPPORTED_REJECTED",
        "WOLF": "UNSUPPORTED_REORGANIZATION_UNVERIFIED",
        "production_PIT_preflight": "REJECTED_WITHOUT_MUTATION",
        "max_absolute_errors": report["component"]["max_absolute_errors"],
        "report_sha256": m.sha256(m.canonical(report)),
        "certification": "UNVERIFIED",
        "full_real_scenario_accepted": False,
        "intraday_execution_replayed": False,
        "strategy_performance_computed": False,
        "isolated_component_broker_invoked": True,
        **b.FALSE_FLAGS,
    }


def execute(destination, source: str, *, verify_only=False) -> dict:
    destination.visibility(True)
    report = report_from_inputs(inputs(destination))
    report["operator_commit"] = source
    summary = public_summary(report)
    ref = "research-data/real-split-probe-v1-" + m.sha(source)
    path = PREFIX + source + "/"
    files = {path + "report.json": m.canonical(report), path + "summary.json": m.canonical(summary)}
    if destination.ref(ref) is None:
        need(not verify_only, "REPORT_NOT_ARCHIVED")
        b.create(destination, ref, files, m.BOOTSTRAP)
    saved, parent = b.read(destination, ref, set(files))
    need(parent == m.BOOTSTRAP and saved == files, "REPORT_READBACK_MISMATCH")
    destination.visibility(True)
    return summary


def guarded() -> str:
    source = m.sha(os.environ.get("REAL_SPLIT_PROBE_REVIEWED_SHA", ""))
    need(
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and os.environ.get("GITHUB_REPOSITORY") == m.CODE
        and os.environ.get("GITHUB_ACTOR") == "dczhhh"
        and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        and os.environ.get("GITHUB_REF") == "refs/heads/main"
        and os.environ.get("GITHUB_SHA") == source
        and os.environ.get("REAL_SPLIT_PROBE_ENABLED") == "true"
        and os.environ.get("ACTIONS_STEP_DEBUG", "").lower() != "true"
        and os.environ.get("ACTIONS_RUNNER_DEBUG", "").lower() != "true",
        "EXECUTION_NOT_AUTHORIZED",
    )
    runtime = Path("runtime").resolve()
    sha = subprocess.check_output(
        ["git", "-C", str(runtime), "rev-parse", "HEAD"], text=True
    ).strip()
    need(sha == RUNTIME_SHA, "RUNTIME_COMMIT_MISMATCH")
    runtime_checked()
    m.GitHub(m.CODE).visibility(False)
    return source


def main() -> int:
    try:
        need(len(sys.argv) == 2 and sys.argv[1] in {"probe", "verify"}, "INVALID_COMMAND")
        source = guarded()
        verify_only = sys.argv[1] == "verify"
        destination = m.GitHub(
            m.DESTINATION, os.environ.get("ARCHIVE_WRITE_TOKEN", ""), writable=not verify_only
        )
        print(
            json.dumps(execute(destination, source, verify_only=verify_only), separators=(",", ":"))
        )
        return 0
    except Exception as error:
        code = str(error) if isinstance(error, Blocked) else "CHECK_FAILED"
        if not re.fullmatch(r"[A-Z_]{1,80}", code):
            code = "CHECK_FAILED"
        print(
            json.dumps(
                {"status": "REAL_SPLIT_PROBE_STOPPED", "code": code, "certification": "UNVERIFIED"},
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
