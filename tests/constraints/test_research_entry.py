"""5E-3 admission/final-use/restart wiring; broader black-box E2E is 5E-4."""

import hashlib
import json
import pickle
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest
from test_verification_orchestration import fixture, freeze, rebind

from ml4t.backtest import BacktestConfig, Engine, Strategy
from ml4t.backtest.config import DataFrequency, ExecutionPrice
from ml4t.backtest.types import ExecutionMode
from quant_constraints import ConstraintConfig, cash_backtest_config
from quant_constraints.research.data import entry
from quant_constraints.research.data.entry import (
    SyntheticRunSettings,
    SyntheticTestEngine,
    build_research_engine,
    build_synthetic_test_engine,
    restore_synthetic_test_engine,
    synthetic_config_sha256,
)
from quant_constraints.research.data.entry_cli import _NoOrders
from quant_constraints.research.data.pit import _canonical
from quant_constraints.research.data.verification_contracts import (
    FrozenDatasetInput,
    VerificationContractError,
    VerifiedDatasetBundle,
)


class Observe(Strategy):
    def __init__(self, tag="fixture"):
        self.tag = tag

    def on_data(self, timestamp, data, context, broker):
        assert "split_adjusted_close" in data["AAA"]["signals"]
        assert "open" in data["AAA"]


def settings(**changes):
    return SyntheticRunSettings(
        cash_backtest_config(**({"initial_cash": 10000} | changes)),
        ConstraintConfig(corporate_actions_enabled=True),
    )


def bound(*, config=None, strategy_type=Observe, **options):
    config = config or settings()
    request, records = fixture(**options)
    request = replace(
        request, strategy_config_sha256=synthetic_config_sha256(config, strategy_type)
    )
    return freeze(request, records), config


def build(snapshot=None, config=None, strategy_type=Observe):
    if snapshot is None:
        snapshot, config = bound(strategy_type=strategy_type)
    return build_synthetic_test_engine(
        snapshot,
        config,
        strategy_type,
        reviewed_root_sha256=getattr(snapshot, "expected_root_sha256", "a" * 64),
    )


def rejects(code, call):
    with pytest.raises(VerificationContractError) as info:
        call()
    assert info.value.code == code
    return info.value


def no_engine(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Rejected admission constructed an Engine/Broker")

    monkeypatch.setattr(entry, "constrained_engine", forbidden)


def test_actual_runtime_repeat_raw_signal_separation_and_provenance():
    snapshot, config = bound()
    first, second = build(snapshot, config).run(), build(snapshot, config).run()
    assert first.equity_curve == second.equity_curve
    audit = first.metrics["data_provenance_v1"]
    assert audit == second.metrics["data_provenance_v1"]
    assert audit["status"] == "offline_entry_contract_pass"
    assert audit["mode"] == "synthetic_test"
    assert audit["domain_validation_performed"] is True
    assert audit["source_authority"] == "unverified"
    assert audit["engine_scope"] == "isolated_synthetic_test"
    assert all(
        audit[key] is False
        for key in (
            "raw_verified",
            "PIT_verified",
            "archive_ready",
            "trusted_entry",
            "may_build_engine",
            "license_verified",
            "coverage_verified",
            "strategy_config_approved",
        )
    )
    assert first.fills == [] and first.equity_curve[0][1] == 10000
    assert audit["input_root_sha256"] == snapshot.expected_root_sha256
    assert (
        hashlib.sha256(
            _canonical({k: v for k, v in audit.items() if k != "audit_sha256"})
        ).hexdigest()
        == audit["audit_sha256"]
    )
    encoded = json.dumps(audit)
    for source in snapshot.sources:
        assert source.source_ref not in encoded
    assert '"open":' not in encoded and '"document":' not in encoded


@pytest.mark.parametrize(
    "value", [None, {}, {"verified": True}, SimpleNamespace(verified=True), object(), b"pickle"]
)
def test_untrusted_build_never_accepts_flags_reports_or_lookalikes(monkeypatch, value):
    no_engine(monkeypatch)
    rejects("INVALID_BUNDLE", lambda: build_research_engine(value))


def test_synthetic_or_unissued_real_bundle_cannot_enter_trusted_builder(monkeypatch):
    snapshot, config = bound()
    no_engine(monkeypatch)
    rejects("SYNTHETIC_NOT_TRUSTED", lambda: build_research_engine(snapshot))
    rejects(
        "TRUSTED_ENTRY_BLOCKED",
        lambda: build_research_engine(object.__new__(VerifiedDatasetBundle)),
    )
    # A fabricated live object must still reconstruct all fields and fail closed.
    empty = object.__new__(FrozenDatasetInput)
    rejects("INVALID_OBJECT_STATE", lambda: build_research_engine(empty))


def test_real_kind_stays_deferred_without_versions_or_runtime(monkeypatch):
    from quant_constraints.research.data.verification_contracts import EvidenceRole

    snapshot, config = bound()
    req = replace(snapshot.request, input_kind="real")
    license_source = replace(
        snapshot.sources[0], role=EvidenceRole.LICENSE, source_id="license", depends_on=()
    )
    real = rebind(req, snapshot.sources + (license_source,))
    no_engine(monkeypatch)
    rejects("REAL_EVIDENCE_DEFERRED", lambda: build_research_engine(real))
    rejects("REAL_EVIDENCE_DEFERRED", lambda: build(real, config))


def test_exact_input_and_config_types_cannot_be_reports_or_subclasses(monkeypatch):
    snapshot, config = bound()
    no_engine(monkeypatch)
    rejects("INVALID_TYPE", lambda: build({}, config))
    rejects("INVALID_TYPE", lambda: build(snapshot, {}))
    rejects("INVALID_TYPE", lambda: SyntheticRunSettings({}, config.constraints))
    rejects("INVALID_TYPE", lambda: SyntheticRunSettings(config.backtest, {}))
    rejects("INVALID_TYPE", lambda: synthetic_config_sha256(config, object))


@pytest.mark.parametrize("parameters", [b"[]", b"null", b'{"x": NaN}', b'{ "x": 1 }'])
def test_parameters_are_strict_bounded_canonical_json(parameters):
    with pytest.raises(VerificationContractError):
        SyntheticRunSettings(settings().backtest, settings().constraints, parameters)


def test_strategy_parameters_are_bound_and_reconstruct_a_fresh_strategy():
    config = replace(settings(), strategy_parameters=b'{"tag":"explicit"}')
    snapshot, _ = bound(config=config)
    assert build(snapshot, config).run().equity_curve[0][1] == 10000
    rejects(
        "CONFIG_BINDING_MISMATCH",
        lambda: build(snapshot, replace(config, strategy_parameters=b'{"tag":"changed"}')),
    )


def test_strategy_source_required(monkeypatch):
    monkeypatch.setattr(
        entry.inspect, "getsource", lambda _: (_ for _ in ()).throw(OSError("private"))
    )
    rejects("STRATEGY_SOURCE_UNAVAILABLE", lambda: synthetic_config_sha256(settings(), Observe))


@pytest.mark.parametrize(
    "change",
    [
        {"data_frequency": DataFrequency.DAILY},
        {"execution_mode": ExecutionMode.SAME_BAR},
        {"execution_price": ExecutionPrice.CLOSE},
        {"enforce_sessions": True},
        {"us_cash_account": False},
        {"allow_short_selling": True},
        {"allow_leverage": True},
        {"cash_buffer_pct": 0.1},
    ],
)
def test_unsupported_runtime_config_rejected_before_engine(monkeypatch, change):
    # Use a config object with fields altered because upstream may reject sooner.
    config = settings()
    for name, value in change.items():
        setattr(config.backtest, name, value)
    snapshot, _ = bound(config=config)
    no_engine(monkeypatch)
    rejects("RUNTIME_CONFIG_UNSUPPORTED", lambda: build(snapshot, config))


@pytest.mark.parametrize(
    "changes", [{"corporate_actions_enabled": False}, {"signal_data_mode": "raw_execution"}]
)
def test_constraints_must_match_bound_runtime_product(monkeypatch, changes):
    config = replace(settings(), constraints=replace(settings().constraints, **changes))
    snapshot, _ = bound(config=config)
    no_engine(monkeypatch)
    rejects("RUNTIME_CONFIG_UNSUPPORTED", lambda: build(snapshot, config))


@pytest.mark.parametrize(
    "options,code",
    [
        ({"frequency": "1d"}, "RUNTIME_SCOPE_UNSUPPORTED"),
        ({"macro": True}, "RUNTIME_FEATURE_UNSUPPORTED"),
        ({"tax": True}, "RUNTIME_FEATURE_UNSUPPORTED"),
    ],
)
def test_supported_runtime_scope_is_explicit_not_a_false_policy_claim(monkeypatch, options, code):
    snapshot, config = bound(**options)
    no_engine(monkeypatch)
    rejects(code, lambda: build(snapshot, config))


def test_delayed_completed_bars_not_silently_used_as_next_open(monkeypatch):
    from quant_constraints.research.data.verification_contracts import EvidenceRole

    request, records = fixture()
    config = settings()
    request = replace(
        request,
        asof=request.asof + timedelta(minutes=1),
        strategy_config_sha256=synthetic_config_sha256(config, Observe),
    )
    for role in (EvidenceRole.RAW, EvidenceRole.COMPARISON):
        records[role] = tuple(
            replace(v, available_at=v.available_at + timedelta(minutes=1)) for v in records[role]
        )
    snapshot = freeze(request, records)
    no_engine(monkeypatch)
    rejects("RUNTIME_SCOPE_UNSUPPORTED", lambda: build(snapshot, config))


def test_later_asof_coverage_cannot_be_used_at_earlier_decision_clock(monkeypatch):
    from test_research_pit import document

    from quant_constraints.research.data.verification_contracts import EvidenceRole

    request, records = fixture()
    config = settings()
    later = request.asof + timedelta(days=1)
    request = replace(
        request,
        asof=later + timedelta(days=1),
        strategy_config_sha256=synthetic_config_sha256(config, Observe),
    )
    records[EvidenceRole.PIT_COVERAGE] = tuple(
        document(replace(v, historically_known_at=later, published_at=later))
        for v in records[EvidenceRole.PIT_COVERAGE]
    )
    snapshot = freeze(request, records)
    no_engine(monkeypatch)
    error = rejects("PIT_COVERAGE_UNKNOWN", lambda: build(snapshot, config))
    assert error.phase == "runtime_pit"


def test_original_root_and_original_domain_validation_cannot_be_replaced(monkeypatch):
    from quant_constraints.research.data.verification_contracts import EvidenceRole

    req, records = fixture()
    config = settings()
    req = replace(req, strategy_config_sha256=synthetic_config_sha256(config, Observe))
    original = freeze(req, records)
    records[EvidenceRole.RAW] = tuple(
        replace(v, open=v.open * 0.9, low=v.low * 0.9) for v in records[EvidenceRole.RAW]
    )
    changed = freeze(req, records)
    no_engine(monkeypatch)
    rejects(
        "SOURCE_ROOT_MISMATCH",
        lambda: build_synthetic_test_engine(
            changed, config, Observe, reviewed_root_sha256=original.expected_root_sha256
        ),
    )
    rejects("RAW_ADJUSTED_PRICE_CONFLICT", lambda: build(changed, config))


def test_input_and_settings_are_copied_then_checked_at_final_use(monkeypatch):
    snapshot, config = bound()
    plan = build(snapshot, config)
    config.backtest.initial_cash = 1
    object.__setattr__(snapshot.request, "input_kind", "real")
    # Caller objects have no alias into the held original immutable plan.
    assert plan.run().equity_curve[0][1] == 10000
    original, config = bound()
    plan = build(original, config)
    plan._settings.backtest.initial_cash = 1
    no_engine(monkeypatch)
    rejects("CONFIG_BINDING_MISMATCH", plan.run)


def test_runtime_software_or_report_mutation_is_detected_before_engine(monkeypatch):
    plan = build()
    no_engine(monkeypatch)
    original = entry._software()
    monkeypatch.setattr(entry, "_software", lambda: original | {"quant_constraints": "f" * 64})
    rejects("RUNTIME_BINDING_MISMATCH", plan.run)
    monkeypatch.setattr(entry, "_software", lambda: original)
    plan._report["raw_verified"] = True
    rejects("RUNTIME_BINDING_MISMATCH", plan.run)


def test_runtime_package_without_source_is_rejected(monkeypatch):
    monkeypatch.setattr(entry.importlib, "import_module", lambda _: SimpleNamespace(__file__=None))
    rejects("RUNTIME_BINDING_MISMATCH", entry._software)


@pytest.mark.parametrize("target", ["prices", "cursor", "config"])
def test_final_engine_feed_mutation_never_runs_strategy_or_ledger(monkeypatch, target):
    actual, engines = entry.constrained_engine, []

    def substitute(*args, **kwargs):
        engine = actual(*args, **kwargs)
        if target == "prices":
            engine.feed.prices = engine.feed.prices.with_columns(entry.pl.lit(0.1).alias("close"))
        elif target == "cursor":
            engine.feed._idx = 1
        else:
            engine.config.initial_cash = 1
        engines.append(engine)
        return engine

    monkeypatch.setattr(entry, "constrained_engine", substitute)
    rejects("RUNTIME_BINDING_MISMATCH", build().run)
    engine = engines[0]
    assert engine.broker.cash == 10000 and not engine.broker.positions
    assert not engine.broker.pending_orders and not engine.equity_curve
    assert not engine._has_run
    rejects(
        "RUNTIME_BINDING_MISMATCH",
        lambda: entry._feed_digest(SimpleNamespace(prices=None, signals=None)),
    )


def test_context_does_not_silently_admit_other_assets(monkeypatch):
    actual, contexts = entry.constrained_engine, []

    def retain(feed, strategy, controller, context, config, **kwargs):
        contexts.append(context)
        return actual(feed, strategy, controller, context, config, **kwargs)

    monkeypatch.setattr(entry, "constrained_engine", retain)
    build().run()
    rejects("RUNTIME_SCOPE_UNSUPPORTED", lambda: contexts[0](None, "OTHER", "submission"))


def test_restart_rechecks_original_input_config_and_software_without_live_state(monkeypatch):
    snapshot, config = bound()
    plan = build(snapshot, config)
    checkpoint = plan.checkpoint()
    no_engine(monkeypatch)
    restored = restore_synthetic_test_engine(
        checkpoint, snapshot, config, Observe, reviewed_root_sha256=snapshot.expected_root_sha256
    )
    assert restored.checkpoint() == checkpoint
    payload = json.loads(checkpoint)
    assert "account" not in payload and "raw" not in payload
    for key, value in [
        ("schema_version", "old"),
        ("input_root_sha256", "b" * 64),
        ("mode", "verified_research"),
        ("state", "resume_account"),
        ("trusted_entry", True),
    ]:
        bad = _canonical(payload | {key: value})
        rejects(
            "CHECKPOINT_BINDING_MISMATCH",
            lambda bad=bad: restore_synthetic_test_engine(
                bad, snapshot, config, Observe, reviewed_root_sha256=snapshot.expected_root_sha256
            ),
        )
    rejects(
        "CHECKPOINT_BINDING_MISMATCH",
        lambda: restore_synthetic_test_engine(
            b"[]", snapshot, config, Observe, reviewed_root_sha256=snapshot.expected_root_sha256
        ),
    )
    rejects(
        "CONFIG_BINDING_MISMATCH",
        lambda: restore_synthetic_test_engine(
            checkpoint,
            snapshot,
            settings(initial_cash=9999),
            Observe,
            reviewed_root_sha256=snapshot.expected_root_sha256,
        ),
    )


def test_completed_and_forged_handles_cannot_restart_or_pickle():
    plan = build()
    rejects("CHECKPOINT_REQUIRED", lambda: pickle.dumps(plan))
    assert not isinstance(plan, VerifiedDatasetBundle)
    rejects("INVALID_BUNDLE", lambda: build_research_engine(plan))
    plan.run()
    rejects("RUN_ALREADY_STARTED", plan.run)
    rejects("RESTART_UNAVAILABLE", plan.checkpoint)
    rejects(
        "INVALID_SYNTHETIC_HANDLE",
        lambda: SyntheticTestEngine(None, None, None, None, None, None, None),
    )
    fake = object.__new__(SyntheticTestEngine)
    rejects("INVALID_SYNTHETIC_HANDLE", fake._check)
    rejects("RUN_ALREADY_STARTED", fake.run)


def test_legacy_engine_remains_usable_and_does_not_claim_trust():
    snapshot, _ = bound()
    feed = entry._feed(snapshot, entry._decode(snapshot))
    result = Engine(feed, _NoOrders(), BacktestConfig(initial_cash=10000)).run()
    assert result.equity_curve[0][1] == 10000
    assert "data_provenance_v1" not in result.metrics
