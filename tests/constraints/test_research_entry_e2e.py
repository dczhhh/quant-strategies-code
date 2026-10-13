"""5E-4 black-box synthetic orders through the reviewed entry, never real trust.

Orders below are literal test instructions, not a production trading signal.
Positive runs do not patch the verifier, provider, Engine, Broker or ledger.
"""

import copy
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import polars as pl
import pytest
from test_raw_validation import KNOWN, freeze_fact
from test_research_entry_files import arguments, cli_files, files_for, load, sha
from test_research_pit import document
from test_verification_orchestration import fixture, freeze, rebind

from ml4t.backtest import OrderStatus, OrderType, Strategy
from ml4t.backtest.execution.limits import VolumeParticipationLimit
from ml4t.backtest.risk import StopLoss
from quant_constraints import ConstraintConfig, cash_backtest_config
from quant_constraints.calendar import NY, SessionCalendar
from quant_constraints.corporate_actions import CorporateActionProcessor
from quant_constraints.research.data import entry
from quant_constraints.research.data.contracts import (
    ActionKind,
    CorporateActionRecord,
    CreditEvidence,
)
from quant_constraints.research.data.entry import (
    SyntheticRunSettings,
    build_research_engine,
    build_synthetic_test_engine,
    restore_synthetic_test_engine,
    synthetic_config_sha256,
)
from quant_constraints.research.data.pit import PITError, PITEventRevision, _canonical
from quant_constraints.research.data.verification_contracts import (
    EvidenceRole,
    VerificationContractError,
)


def at(day="2025-11-14", clock="15:01"):
    return datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=NY).astimezone(UTC)


def economic_state(broker):
    """Observe existing economic state, including reservations and nonempty ledgers."""
    return copy.deepcopy(
        {
            "account": {k: v for k, v in vars(broker.account).items() if k != "policy"},
            "cash": broker.cash,
            "settled": broker.settled_cash,
            "unsettled": broker.unsettled_cash,
            "reserved": broker.reserved_cash,
            "positions": broker.positions,
            "orders": [vars(o) for o in broker.orders],
            "fills": broker.fills,
            "trades": broker.trades,
            "fees": broker.fee_records,
            "slippage": broker.slippage_records,
            "fee_volume": broker.fee_model.volume("2025-11") if broker.fee_model else 0,
            "corporate_state": broker.corporate_action_processor.state,
            "time": broker._market_state.time,
        }
    )


class ScriptOrders(Strategy):
    """Declared test orders plus read-only observations of the actual Broker."""

    instances = []

    def __init__(self, instructions=None, participation=None, stop=None):
        self.instructions = instructions or []
        self.participation, self.stop = participation, stop
        self.orders, self.observations, self.prices = {}, {}, {}
        self.broker = None
        self.started = False
        type(self).instances.append(self)

    def on_start(self, broker):
        self.broker, self.started = broker, True
        if self.participation is not None:
            broker.execution_limits = VolumeParticipationLimit(self.participation)
        if self.stop is not None:
            broker.set_position_rules(StopLoss(self.stop))

    def on_data(self, timestamp, data, context, broker):
        stamp = timestamp.isoformat()
        self.prices[stamp] = (data["AAA"]["open"], data["AAA"]["signals"]["split_adjusted_close"])
        self.observations[stamp] = {
            "cash": broker.cash,
            "settled": broker.settled_cash,
            "unsettled": broker.unsettled_cash,
            "reserved": broker.reserved_cash,
            "quantity": broker.account.get_position_quantity("AAA"),
            "receivable": broker.account._receivable_value,
            "fills": len(broker.fills),
        }
        for instruction in self.instructions:
            if instruction["at"] != stamp:
                continue
            label, operation = instruction["label"], instruction.get("operation", "order")
            if operation == "bracket":
                self.orders[label] = broker.submit_bracket(
                    "AAA",
                    instruction["quantity"],
                    take_profit=instruction["take_profit"],
                    stop_loss=instruction["stop_loss"],
                )
            elif operation == "cancel":
                assert broker.cancel_order(self.orders[instruction["target"]].order_id)
            else:
                self.orders[label] = broker.submit_order(
                    "AAA",
                    instruction["quantity"],
                    order_type=OrderType.LIMIT
                    if "limit_price" in instruction
                    else OrderType.MARKET,
                    limit_price=instruction.get("limit_price"),
                )


@pytest.fixture(autouse=True)
def clear_observers():
    ScriptOrders.instances.clear()
    yield
    ScriptOrders.instances.clear()


def instruction(label, quantity, *, day="2025-11-14", clock="15:01", **extra):
    return {"label": label, "quantity": quantity, "at": at(day, clock).isoformat(), **extra}


def case(
    instructions=(),
    *,
    start=None,
    end=None,
    dividend=False,
    constraints=None,
    drop=None,
    ratio=10,
    macro=False,
    tax=False,
    **parameters,
):
    """Complete invented RTH minutes, independent split fact and original PIT graph."""
    request, records = fixture(macro=macro, tax=tax)
    if ratio != 10:
        split = replace(records[EvidenceRole.ACTIONS][0], split_ratio=ratio)
        records[EvidenceRole.ACTIONS] = (split,)
        records[EvidenceRole.PIT_EVENTS] = (
            document(replace(records[EvidenceRole.PIT_EVENTS][0], record=split)),
        )
        fact = records[EvidenceRole.FACTS][0]
        records[EvidenceRole.FACTS] = (
            freeze_fact(
                replace(
                    fact,
                    new_shares=int(ratio) if ratio >= 1 else 1,
                    old_shares=1 if ratio >= 1 else int(1 / ratio),
                )
            ),
        )
    opening, end = start or at(clock="15:00"), end or at(clock="15:10")
    raw_template = records[EvidenceRole.RAW][0]
    comparison_template = records[EvidenceRole.COMPARISON][0]
    raw, comparison = [], []
    day, calendar = opening.astimezone(NY).date(), SessionCalendar()
    while day <= end.astimezone(NY).date():
        session = calendar.session(day)
        if session is not None:
            start = max(opening, session.market_open.astimezone(UTC))
            closing = min(end, session.market_close.astimezone(UTC))
            while start < closing:
                finish = start + timedelta(minutes=1)
                factor = ratio if day < date(2025, 11, 17) else 1
                signal_price = (
                    80 if drop is not None and start + timedelta(minutes=1) >= drop else 100
                )
                price = signal_price * factor
                common = {"bar_start_at": start, "bar_end_at": finish, "available_at": finish}
                raw.append(
                    replace(
                        raw_template,
                        **common,
                        source=replace(raw_template.source, record_id=f"raw-{len(raw)}"),
                        open=price,
                        high=price * 1.01,
                        low=price * 0.99,
                        close=price,
                        volume=100,
                    )
                )
                comparison.append(
                    replace(
                        comparison_template,
                        **common,
                        source=replace(
                            comparison_template.source, record_id=f"signal-{len(comparison)}"
                        ),
                        open=signal_price,
                        high=signal_price * 1.01,
                        low=signal_price * 0.99,
                        close=signal_price,
                        volume=100 * factor,
                    )
                )
                start = finish
        day += timedelta(days=1)
    assert raw and raw[-1].bar_end_at == end
    records[EvidenceRole.RAW], records[EvidenceRole.COMPARISON] = tuple(raw), tuple(comparison)
    definition = records[EvidenceRole.DEFINITION][0]
    records[EvidenceRole.DEFINITION] = (
        replace(
            definition,
            window_start=opening,
            window_end=end,
            adjustment_anchor=max(definition.adjustment_anchor, end),
            action_coverage_end=max(definition.action_coverage_end, end + timedelta(days=1)),
        ),
    )
    if dividend:
        split = records[EvidenceRole.ACTIONS][0]
        div = CorporateActionRecord(
            security_id=request.security_id,
            event_id="dividend",
            kind=ActionKind.CASH_DIVIDEND,
            effective_date=date(2025, 11, 17),
            ex_date=date(2025, 11, 17),
            payable_date=date(2025, 11, 18),
            dividend_per_share=1,
            historically_known_at=KNOWN,
            knowledge_evidence="fixture://dividend",
            event_version=1,
            source=split.source,
            currency="USD",
        )
        credit = CorporateActionRecord(
            security_id=request.security_id,
            event_id="credit",
            kind=ActionKind.CASH_CREDIT,
            effective_date=date(2025, 11, 18),
            credit_date=date(2025, 11, 18),
            parent_event_id="dividend",
            credited_net=150,
            credit_evidence=CreditEvidence.SIMULATED_ASSUMPTION,
            credit_evidence_ref="fixture://invented-credit",
            historically_known_at=KNOWN,
            knowledge_evidence="fixture://invented-credit",
            event_version=1,
            source=split.source,
            currency="USD",
        )
        records[EvidenceRole.PIT_EVENTS] += tuple(
            document(
                PITEventRevision(
                    record=r,
                    published_at=KNOWN,
                    provider_available_at=None,
                    document=b"",
                    document_sha256="",
                )
            )
            for r in (div, credit)
        )
    settings = SyntheticRunSettings(
        cash_backtest_config(initial_cash=100000),
        constraints
        or ConstraintConfig(
            corporate_actions_enabled=True,
            missing_earnings="allow",
            missing_earnings_position="hold",
            unknown_industry="warn",
            drift_reduction="disabled",
        ),
        strategy_parameters=_canonical({"instructions": list(instructions), **parameters}),
    )
    request = replace(
        request,
        window_start=opening,
        window_end=end,
        asof=end,
        strategy_config_sha256=synthetic_config_sha256(settings, ScriptOrders),
    )
    return freeze(request, records), settings, records


def plan(snapshot, settings):
    return build_synthetic_test_engine(
        snapshot, settings, ScriptOrders, reviewed_root_sha256=snapshot.expected_root_sha256
    )


def run(snapshot, settings):
    result = plan(snapshot, settings).run()
    probe = ScriptOrders.instances[-1]
    assert probe.started and probe.broker is not None
    audit = result.metrics["data_provenance_v1"]
    assert audit["status"] == "offline_entry_contract_pass"
    assert all(
        audit[key] is False
        for key in (
            "raw_verified",
            "PIT_verified",
            "archive_ready",
            "trusted_entry",
            "may_build_engine",
        )
    )
    return result, probe


def test_orders_replay_raw_prices_real_fees_slippage_and_restore_deterministically(tmp_path):
    snapshot, settings, _ = case([instruction("buy", 15), instruction("sell", -15, clock="15:03")])
    root, snapshot, _, manifest = files_for(tmp_path, snapshot)
    files = load(root, snapshot, manifest)
    original = build_synthetic_test_engine(
        files, settings, ScriptOrders, reviewed_root_sha256=snapshot.expected_root_sha256
    )
    checkpoint = original.checkpoint()
    restored = restore_synthetic_test_engine(
        checkpoint,
        files,
        settings,
        ScriptOrders,
        reviewed_root_sha256=snapshot.expected_root_sha256,
    )
    results, states = [], []
    for p in (original, restored, plan(snapshot, settings)):
        result = p.run()
        results.append(result)
        states.append(economic_state(ScriptOrders.instances[-1].broker))
        assert len(result.fills) == 2
        assert [f.price for f in result.fills] == pytest.approx([1000.5, 999.5])
        assert result.fills[0].timestamp == at(clock="15:02")
        assert result.fills[1].timestamp == at(clock="15:04")
        assert result.metrics["total_commission"] > 0
        assert not ScriptOrders.instances[-1].broker.positions
        assert result.metrics["final_value"] < 100000
    assert states[0] == states[1] == states[2]
    for result in results[1:]:
        assert result.equity_curve == results[0].equity_curve
        assert result.portfolio_state == results[0].portfolio_state
        assert result.fills == results[0].fills
        assert result.metrics["data_provenance_v1"] == results[0].metrics["data_provenance_v1"]
    audit_text = json.dumps(results[0].metrics["data_provenance_v1"])
    assert str(root) not in audit_text and "fixture://" not in audit_text


def test_real_fills_then_split_dividend_receivable_and_credit_each_once():
    snapshot, settings, _ = case(
        [instruction("buy", 15)], end=at("2025-11-18", "09:33"), dividend=True
    )
    result, probe = run(snapshot, settings)
    assert len(result.fills) == 1
    assert probe.broker.positions["AAA"].quantity == 150
    assert probe.broker.positions["AAA"].entry_price == pytest.approx(100.05)
    monday = probe.observations[at("2025-11-17", "09:31").isoformat()]
    tuesday = probe.observations[at("2025-11-18", "09:31").isoformat()]
    assert monday["receivable"] == 150
    assert tuesday["receivable"] == 0
    assert tuesday["cash"] == pytest.approx(monday["cash"] + 150)
    evidence = result.metrics["corporate_actions_v1"]
    assert len(evidence["records"]) == 3
    assert [r["kind"] for r in evidence["records"]] == ["SPLIT", "CASH_DIVIDEND", "CASH_CREDIT"]
    assert probe.prices[at(clock="15:01").isoformat()] == (1000, 100)
    assert probe.prices[at("2025-11-17", "09:31").isoformat()] == (100, 100)
    assert not probe.broker.account._receivables


def test_partial_bracket_split_preserves_actual_exposure_oco_and_fee_commit_once():
    constraints = ConstraintConfig(
        corporate_actions_enabled=True,
        missing_earnings="allow",
        missing_earnings_position="hold",
        unknown_industry="warn",
        drift_reduction="disabled",
        buy_time_in_force="GTD",
        max_defer_sessions=5,
    )
    snapshot, settings, _ = case(
        [
            instruction("protect", 20, operation="bracket", take_profit=1200, stop_loss=900),
        ],
        end=at("2025-11-17", "10:03"),
        participation=0.0005,
        constraints=constraints,
    )
    result, probe = run(snapshot, settings)
    parent, take, stop = probe.orders["protect"]
    assert parent.status is take.status is stop.status is OrderStatus.PENDING
    assert take.quantity == stop.quantity == probe.broker.positions["AAA"].quantity
    assert take.limit_price == 120 and stop.stop_price == 90
    before = [f for f in result.fills if f.timestamp < at("2025-11-17", "09:30")]
    after = [f for f in result.fills if f.timestamp >= at("2025-11-17", "09:30")]
    assert before and after and all(f.quantity == pytest.approx(0.05) for f in result.fills)
    assert parent.filled_quantity == pytest.approx(
        10 * sum(f.quantity for f in before) + sum(f.quantity for f in after)
    )
    assert probe.broker.positions["AAA"].quantity == parent.filled_quantity < 200
    assert len(probe.broker.fee_records) == len(result.fills)
    # Existing fractional-share pricing is 1% of executed value, not the whole-share minimum.
    assert sum(r.fees.broker_commission for r in probe.broker.fee_records) == pytest.approx(
        sum(f.quantity * f.price * 0.01 for f in result.fills)
    )
    assert probe.broker.fee_model.volume("2025-11") == pytest.approx(
        sum(f.quantity for f in result.fills)
    )
    assert len(result.metrics["corporate_actions_v1"]["records"]) == 1


def test_default_missing_evidence_and_long_only_reject_without_economic_change():
    defaults = ConstraintConfig(corporate_actions_enabled=True)
    snapshot, settings, _ = case(
        [instruction("buy", 15), instruction("short", -1, clock="15:02")], constraints=defaults
    )
    result, probe = run(snapshot, settings)
    assert not result.fills and not probe.broker.positions
    assert probe.broker.cash == probe.broker.settled_cash == 100000
    assert not probe.broker.fee_records and probe.broker.reserved_cash == 0
    assert probe.orders["buy"].status is OrderStatus.REJECTED
    assert probe.orders["short"].status is OrderStatus.REJECTED
    assert probe.orders["buy"].rejection_code == "earnings_coverage_missing"


def test_actual_oco_stop_cancels_sibling_without_naked_or_short_position():
    snapshot, settings, _ = case(
        [
            instruction("protect", 20, operation="bracket", take_profit=1200, stop_loss=900),
        ],
        drop=at(clock="15:04"),
    )
    result, probe = run(snapshot, settings)
    parent, take, stop = probe.orders["protect"]
    assert parent.status is stop.status is OrderStatus.FILLED
    assert take.status is OrderStatus.CANCELLED
    assert not probe.broker.positions and not probe.broker.get_pending_orders()
    assert len(result.fills) == len(probe.broker.fee_records) == 2
    assert result.fills[-1].quantity == 20
    assert result.fills[-1].price == pytest.approx(799.6)
    assert probe.broker.reserved_cash == 0


def test_cash_sale_proceeds_cannot_be_reused_before_calendar_settlement():
    constraints = ConstraintConfig(
        corporate_actions_enabled=True,
        missing_earnings="allow",
        missing_earnings_position="hold",
        unknown_industry="warn",
        drift_reduction="disabled",
        cash_reserve=0,
        equity_target=1,
        max_weight=1,
        industry_cap=1,
        min_target_weight=0.001,
    )
    snapshot, settings, _ = case(
        [
            instruction("buy", 99.5),
            instruction("sell", -99.5, clock="15:03"),
            instruction("reuse", 1, clock="15:05"),
            instruction("settled_buy", 10, day="2025-11-17", clock="10:00"),
        ],
        end=at("2025-11-17", "10:03"),
        constraints=constraints,
    )
    result, probe = run(snapshot, settings)
    assert probe.orders["buy"].status is probe.orders["sell"].status is OrderStatus.FILLED
    assert probe.orders["reuse"].status is OrderStatus.REJECTED
    assert probe.orders["reuse"].rejection_code == "settled_cash"
    friday = probe.observations[at(clock="15:05").isoformat()]
    monday = probe.observations[at("2025-11-17", "09:31").isoformat()]
    assert friday["unsettled"] > 98000 and friday["settled"] < 1000
    assert monday["unsettled"] == 0 and monday["settled"] == pytest.approx(monday["cash"])
    assert probe.orders["settled_buy"].status is OrderStatus.FILLED
    assert len(result.fills) == 3 and probe.broker.positions["AAA"].quantity == 10
    assert probe.broker.cash >= 0 and probe.broker.settled_cash >= 0


@pytest.mark.parametrize("clock,code", [("09:31", "entry_window"), ("15:31", "entry_window")])
def test_rth_and_entry_buffers_are_enforced_by_real_order_submission(clock, code):
    start = at(clock="09:30") if clock == "09:31" else at(clock="15:00")
    end = at(clock="09:35") if clock == "09:31" else at(clock="16:00")
    snapshot, settings, _ = case([instruction("buy", 15, clock=clock)], start=start, end=end)
    result, probe = run(snapshot, settings)
    assert probe.orders["buy"].status is OrderStatus.REJECTED
    assert probe.orders["buy"].rejection_code == code
    assert not result.fills and not probe.broker.positions
    assert probe.broker.cash == probe.broker.settled_cash == 100000
    assert probe.broker.reserved_cash == 0 and not probe.broker.fee_records


@pytest.mark.parametrize("fault", ["provider", "after_apply_split", "after_apply_credit"])
def test_runtime_fault_preserves_nonempty_account_orders_fills_and_costs(monkeypatch, fault):
    credit = fault == "after_apply_credit"
    constraints = ConstraintConfig(
        corporate_actions_enabled=True,
        missing_earnings="allow",
        missing_earnings_position="hold",
        unknown_industry="warn",
        drift_reduction="disabled",
        buy_time_in_force="GTD",
        max_defer_sessions=5,
    )
    snapshot, settings, _ = case(
        [
            instruction("buy", 15),
            instruction("pending", 1, clock="15:03", limit_price=800),
        ],
        end=at("2025-11-18", "09:33") if credit else at("2025-11-17", "10:03"),
        dividend=credit,
        constraints=constraints,
    )
    original = CorporateActionProcessor.apply
    original_prepare = CorporateActionProcessor.prepare
    failed_at = at("2025-11-18", "09:31") if credit else at("2025-11-17", "09:31")
    before = []

    def prepare(processor, timestamp, observed):
        if timestamp == failed_at:
            # Observe before the failed bar, not after its clock/marks moved.
            before.append(economic_state(processor.broker))
            if fault == "provider":
                raise PITError("PIT_COVERAGE_UNKNOWN", "coverage")
        return original_prepare(processor, timestamp, observed)

    def failure(processor, timestamp, observed, prepared):
        if timestamp != failed_at:
            return original(processor, timestamp, observed, prepared)
        # Broker._update_time already owns its transactional snapshot.
        original(processor, timestamp, observed, prepared)
        raise ValueError("synthetic injected post-action failure")

    # Fault injection calls the actual processor, including its economic mutation.
    monkeypatch.setattr(CorporateActionProcessor, "apply", failure)
    monkeypatch.setattr(CorporateActionProcessor, "prepare", prepare)
    p = plan(snapshot, settings)
    with pytest.raises((PITError, ValueError)):
        p.run()
    probe = ScriptOrders.instances[-1]
    assert probe.started and len(probe.broker.fills) == 1 and probe.broker.positions
    assert probe.broker.fee_records and before
    assert probe.orders["pending"].status is OrderStatus.PENDING
    assert probe.broker.reserved_cash > 0
    if credit:
        assert probe.broker.account._receivable_value == 150
    # The failed bar is rolled back; prior successful bars remain committed.
    after = economic_state(probe.broker)
    assert after == before[0]
    assert probe.broker.corporate_action_processor.failures
    with pytest.raises(VerificationContractError, match="RUN_ALREADY_STARTED"):
        p.run()
    with pytest.raises(VerificationContractError, match="RESTART_UNAVAILABLE"):
        p.checkpoint()


@pytest.mark.parametrize(
    "role",
    [
        EvidenceRole.RAW,
        EvidenceRole.COMPARISON,
        EvidenceRole.ACTIONS,
        EvidenceRole.IDENTITIES,
        EvidenceRole.FACTS,
        EvidenceRole.DEFINITION,
        EvidenceRole.PIT_EVENTS,
        EvidenceRole.PIT_IDENTITIES,
        EvidenceRole.PIT_COVERAGE,
    ],
)
@pytest.mark.parametrize("phase", ["run", "restore"])
def test_file_tampering_blocks_order_capable_plan_before_allocation(
    tmp_path, monkeypatch, role, phase
):
    snapshot, settings, _ = case([instruction("buy", 15)])
    root, snapshot, paths, manifest = files_for(tmp_path, snapshot)
    files = load(root, snapshot, manifest)
    p = build_synthetic_test_engine(
        files, settings, ScriptOrders, reviewed_root_sha256=snapshot.expected_root_sha256
    )
    checkpoint = p.checkpoint()
    before = ScriptOrders.instances.copy()
    path = root / paths[role.value]
    payload = json.loads(path.read_bytes())
    payload["private-injected-verified-claim"] = True
    path.write_bytes(_canonical(payload))
    allocations = []
    actual = entry.constrained_engine

    def record_allocation(*args, **kwargs):
        allocations.append(True)
        return actual(*args, **kwargs)

    monkeypatch.setattr(entry, "constrained_engine", record_allocation)
    with pytest.raises(VerificationContractError) as info:
        if phase == "run":
            p.run()
        else:
            restore_synthetic_test_engine(
                checkpoint,
                files,
                settings,
                ScriptOrders,
                reviewed_root_sha256=snapshot.expected_root_sha256,
            )
    assert info.value.code == "CONTENT_HASH_MISMATCH"
    assert ScriptOrders.instances == before and not allocations


@pytest.mark.parametrize(
    "role,field,value",
    [
        (EvidenceRole.RAW, "open", 100),
        (EvidenceRole.RAW, "price_basis", "split_adjusted"),
        (EvidenceRole.RAW, "share_unit", "split_adjusted"),
        (EvidenceRole.RAW, "volume_unit", "split_adjusted_shares"),
        (EvidenceRole.COMPARISON, "close", 1000),
        (EvidenceRole.DEFINITION, "volume_policy", "unknown"),
    ],
)
def test_rehashed_wrong_price_share_volume_products_still_refuse_before_engine(
    monkeypatch, role, field, value
):
    snapshot, settings, _ = case([instruction("buy", 15)])
    sources = []
    for source in snapshot.sources:
        if source.role is role:
            payload = json.loads(source.content)
            payload["records"][0][field] = value
            body = _canonical(payload)
            source = replace(source, content=body, sha256=hashlib.sha256(body).hexdigest())
        sources.append(source)
    changed = rebind(snapshot.request, tuple(sources))
    with pytest.raises(VerificationContractError, match="SOURCE_ROOT_MISMATCH"):
        build_synthetic_test_engine(
            changed, settings, ScriptOrders, reviewed_root_sha256=snapshot.expected_root_sha256
        )
    # Even an explicitly supplied new root cannot make these invalid domain bytes pass.
    allocated = []
    original = entry.constrained_engine

    def record(*args, **kwargs):
        allocated.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(entry, "constrained_engine", record)
    with pytest.raises(VerificationContractError) as info:
        plan(changed, settings)
    assert info.value.phase in {"decode", "normalize", "raw"}
    assert not allocated and not ScriptOrders.instances


def test_order_capable_success_result_report_and_handle_cannot_unlock_real_entry():
    snapshot, settings, _ = case([instruction("buy", 15)])
    p = plan(snapshot, settings)
    result = p.run()
    assert result.fills
    for false_credential in (
        p,
        result,
        result.metrics["data_provenance_v1"],
        {"verified": True, "trusted_entry": True},
        snapshot,
    ):
        with pytest.raises(VerificationContractError):
            build_research_engine(false_credential)


def test_closed_session_order_fails_before_cash_positions_orders_or_cost_mutation():
    snapshot, settings, _ = case([instruction("buy", 15, clock="16:00")], end=at(clock="16:00"))
    p = plan(snapshot, settings)
    # Existing DAY API rejects a non-future validity deadline before the order gates.
    with pytest.raises(ValueError, match="Order valid_until must be in the future"):
        p.run()
    broker = ScriptOrders.instances[-1].broker
    assert broker.cash == broker.settled_cash == 100000
    assert not broker.positions and not broker.orders and not broker.fills
    assert not broker.fee_records and broker.reserved_cash == 0


@pytest.mark.parametrize("ratio,quantity", [(10, 15), (2, 75), (0.1, 1500)])
def test_forward_reverse_split_keeps_raw_stop_units_and_equity_without_fake_fill(ratio, quantity):
    snapshot, settings, _ = case(
        [instruction("buy", quantity)], ratio=ratio, end=at("2025-11-17", "10:03"), stop=0.1
    )
    result, probe = run(snapshot, settings)
    position = probe.broker.positions["AAA"]
    assert len(result.fills) == 1
    assert position.quantity == pytest.approx(quantity * ratio)
    assert position.entry_price == pytest.approx(100.05)
    before = probe.observations[at(clock="15:03").isoformat()]
    after = probe.observations[at("2025-11-17", "09:31").isoformat()]
    assert before["cash"] == after["cash"]
    assert before["quantity"] * 100 * ratio == pytest.approx(after["quantity"] * 100)
    assert len(result.metrics["corporate_actions_v1"]["records"]) == 1


@pytest.mark.parametrize("fault", ["feed", "config", "file"])
def test_final_use_rejection_preserves_allocated_broker_before_first_order(
    tmp_path, monkeypatch, fault
):
    snapshot, settings, _ = case([instruction("buy", 15)])
    root, snapshot, paths, manifest = files_for(tmp_path, snapshot)
    files = load(root, snapshot, manifest)
    p = build_synthetic_test_engine(
        files, settings, ScriptOrders, reviewed_root_sha256=snapshot.expected_root_sha256
    )
    actual = entry.constrained_engine
    retained = []

    def inject(*args, **kwargs):
        engine = actual(*args, **kwargs)
        retained.append((engine, economic_state(engine.broker)))
        if fault == "feed":
            engine.feed.prices = engine.feed.prices.with_columns(
                (pl.col("open") * 0.1).alias("open")
            )
        elif fault == "config":
            engine.config.initial_cash = 99999
        else:
            (root / paths["raw"]).write_bytes(b"private-injected-source")
        return engine

    monkeypatch.setattr(entry, "constrained_engine", inject)
    with pytest.raises(VerificationContractError) as info:
        p.run()
    assert info.value.code == (
        "CONTENT_HASH_MISMATCH" if fault == "file" else "RUNTIME_BINDING_MISMATCH"
    )
    assert len(retained) == 1
    engine, before = retained[0]
    assert economic_state(engine.broker) == before
    assert not engine._has_run and not engine.equity_curve
    assert not ScriptOrders.instances[-1].started


@pytest.mark.parametrize("feature", ["macro", "tax"])
def test_uninstalled_sibling_runtime_policy_refuses_order_capable_plan(feature):
    snapshot, settings, _ = case([instruction("buy", 15)], **{feature: True})
    with pytest.raises(VerificationContractError, match="RUNTIME_FEATURE_UNSUPPORTED"):
        plan(snapshot, settings)
    assert not ScriptOrders.instances


@pytest.mark.parametrize(
    "mode,exit_code,code",
    [
        ("synthetic_test", 0, "SYNTHETIC_RUN_COMPLETE"),
        ("verified_research", 2, "SYNTHETIC_NOT_TRUSTED"),
        (None, 2, "INVALID_CLI_ARGUMENTS"),
    ],
)
def test_cli_new_process_explicit_modes_are_deterministic_and_redacted(
    tmp_path, mode, exit_code, code
):
    root, snapshot, manifest, config = cli_files(tmp_path.resolve())
    args = arguments(root, snapshot, manifest, mode or "synthetic_test") + [
        "--config",
        "config.json",
        "--config-sha256",
        sha(config),
    ]
    if mode is None:
        args = args[2:]
    outputs = []
    for seed in ("0", "123"):
        completed = subprocess.run(
            [sys.executable, "-m", "quant_constraints.research.data.entry_cli", *args],
            cwd=root,
            env=os.environ | {"PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert completed.returncode == exit_code and not completed.stderr
        audit = json.loads(completed.stdout)
        assert audit["code"] == code
        assert all(
            audit[key] is False
            for key in (
                "raw_verified",
                "PIT_verified",
                "archive_ready",
                "trusted_entry",
                "may_build_engine",
            )
        )
        assert str(root) not in completed.stdout and "fixture://" not in completed.stdout
        outputs.append(audit)
    assert outputs[0] == outputs[1]
