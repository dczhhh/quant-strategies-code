"""5E-3 opt-in admission and a bounded, isolated synthetic runtime plan.

Real trust remains unavailable. Legacy Engine APIs are unchanged. Integrity
checks do not defend against arbitrary Python execution inside this process.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import inspect
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Never

import polars as pl

from ml4t.backtest import BacktestConfig, DataFeed, Strategy
from ml4t.backtest.config import DataFrequency, ExecutionPrice
from ml4t.backtest.types import ExecutionMode
from quant_constraints import (
    ConstraintConfig,
    ConstraintController,
    InMemoryEarningsProvider,
    MarketContext,
    constrained_engine,
)

from .entry_files import FrozenDatasetFiles
from .pit import PITDataset, PITError, SyntheticPITProvider, _canonical, pit_input_sha256
from .provenance import ResearchMode
from .verification import _decode, _normalize, verify_dataset
from .verification_contracts import (
    EvidenceRole,
    FrozenDatasetInput,
    VerifiedDatasetBundle,
    _deny,
    _reconstruct,
)
from .verification_wire import decode_json, safe_field

ENTRY_VERSION = "research_entry_synthetic_v1"
CHECKPOINT_SCHEMA = "synthetic_research_restart_v1"
_TOKEN = object()


@dataclass(frozen=True, slots=True)
class SyntheticRunSettings:
    backtest: BacktestConfig
    constraints: ConstraintConfig
    strategy_parameters: bytes = b"{}"

    def __post_init__(self):
        if (
            type(self.backtest) is not BacktestConfig
            or type(self.constraints) is not ConstraintConfig
        ):
            _deny("INVALID_TYPE", "config", "entry")
        parameters = decode_json(self.strategy_parameters)
        if type(parameters) is not dict or _canonical(parameters) != self.strategy_parameters:
            _deny("INVALID_CONFIG", "strategy_parameters", "entry")
        object.__setattr__(self, "backtest", copy.deepcopy(self.backtest))
        object.__setattr__(self, "constraints", replace(self.constraints))


def _strategy_binding(strategy_type: type[Strategy]) -> dict:
    if not isinstance(strategy_type, type) or not issubclass(strategy_type, Strategy):
        _deny("INVALID_TYPE", "strategy", "entry")
    try:
        content = inspect.getsource(strategy_type).encode()
    except (OSError, TypeError):
        _deny("STRATEGY_SOURCE_UNAVAILABLE", "strategy", "entry")
    return {
        "module": strategy_type.__module__,
        "qualname": strategy_type.__qualname__,
        "source_sha256": hashlib.sha256(content.replace(b"\r\n", b"\n")).hexdigest(),
    }


def synthetic_config_sha256(settings: SyntheticRunSettings, strategy_type: type[Strategy]) -> str:
    """Actual config/strategy binding; a digest does not approve strategy policy."""
    settings = _reconstruct(settings, SyntheticRunSettings, "config")
    return hashlib.sha256(
        _canonical(
            {
                "entry_version": ENTRY_VERSION,
                "backtest": settings.backtest.to_dict(),
                "constraints": asdict(settings.constraints),
                "strategy": _strategy_binding(strategy_type),
                "strategy_parameters": decode_json(settings.strategy_parameters),
            }
        )
    ).hexdigest()


def _software() -> dict:
    """Bind runtime dependency source trees, including their internal collaborators."""
    result = {}
    for package in ("ml4t.backtest", "quant_constraints", "ml4t.specs"):
        module = importlib.import_module(package)
        if module.__file__ is None:
            _deny("RUNTIME_BINDING_MISMATCH", "software", "entry")
        directory = Path(module.__file__).parent
        sources = {
            path.relative_to(directory).as_posix(): hashlib.sha256(
                path.read_bytes().replace(b"\r\n", b"\n")
            ).hexdigest()
            for path in sorted(directory.rglob("*.py"))
            if path.name != "_version.py"
        }
        result[package] = hashlib.sha256(_canonical(sources)).hexdigest()
    return result


def _snapshot(source: FrozenDatasetInput | FrozenDatasetFiles, root: str) -> FrozenDatasetInput:
    if type(source) is FrozenDatasetFiles:
        snapshot = source.reload(root)
    else:
        snapshot = _reconstruct(source, FrozenDatasetInput, "snapshot")
        snapshot.assert_bound(root)
    return snapshot


def _preflight(source, root, settings, strategy_type) -> tuple[FrozenDatasetInput, dict, dict]:
    snapshot = _snapshot(source, root)
    if snapshot.request.input_kind == "synthetic" and (
        snapshot.request.macro_enabled or snapshot.request.dividend_tax_enabled
    ):
        _deny("RUNTIME_FEATURE_UNSUPPORTED", "feature_policy", "entry")
    report = verify_dataset(snapshot, ResearchMode.SYNTHETIC_TEST, reviewed_root_sha256=root)
    if report["status"] != "synthetic_contract_pass" or not report["domain_validation_performed"]:
        _deny(report["code"], report["field"], report["phase"])
    if synthetic_config_sha256(settings, strategy_type) != snapshot.request.strategy_config_sha256:
        _deny("CONFIG_BINDING_MISMATCH", "strategy_config_sha256", "entry")
    request, config, constraints = snapshot.request, settings.backtest, settings.constraints
    # No unchecked daily conversion or pretend PR #8 runtime policy injection.
    # These reviewed sibling gates are still exercised by the pinned integration.
    if request.frequency != "1m":
        _deny("RUNTIME_SCOPE_UNSUPPORTED", "frequency", "entry")
    if (
        config.resolved_data_frequency is not DataFrequency.MINUTE_1
        or config.execution_mode is not ExecutionMode.NEXT_BAR
        or config.execution_price is not ExecutionPrice.OPEN
        or config.enforce_sessions
        or not config.us_cash_account
        or config.allow_short_selling
        or config.allow_leverage
        or config.cash_buffer_pct != 0
        or not constraints.corporate_actions_enabled
        or constraints.execution_data_mode != "raw_execution"
        or constraints.signal_data_mode != "split_adjusted_signal"
    ):
        _deny("RUNTIME_CONFIG_UNSUPPORTED", "config", "entry")
    records = _decode(snapshot)
    value = _normalize(records, request)
    if any(
        bar.available_at != bar.bar_end_at or bar.symbol != request.symbol
        for bar in (*value.raw, *value.comparison)
    ):
        _deny("RUNTIME_SCOPE_UNSUPPORTED", "bar_availability", "entry")
    provider = _provider(records)
    try:
        for bar in value.raw:
            provider.snapshot(request.security_id, bar.available_at)
            provider.resolve_asof(bar.symbol, bar.venue, bar.bar_start_at, bar.available_at)
    except PITError as error:
        _deny(error.code, safe_field(error.field), "runtime_pit")
    return snapshot, report, records


def _provider(records: dict) -> SyntheticPITProvider:
    dataset = PITDataset(
        events=records[EvidenceRole.PIT_EVENTS],
        identities=records[EvidenceRole.PIT_IDENTITIES],
        coverage=records[EvidenceRole.PIT_COVERAGE],
        input_kind="synthetic",
        expected_sha256="",
    )
    return SyntheticPITProvider(replace(dataset, expected_sha256=pit_input_sha256(dataset)))


def _feed(snapshot: FrozenDatasetInput, records: dict) -> DataFeed:
    value = _normalize(records, snapshot.request)
    prices = pl.DataFrame(
        [
            {
                "timestamp": bar.available_at,
                "asset": bar.symbol,
                **{name: getattr(bar, name) for name in ("open", "high", "low", "close", "volume")},
            }
            for bar in value.raw
        ]
    )
    signals = pl.DataFrame(
        [
            {
                "timestamp": bar.available_at,
                "asset": bar.symbol,
                "split_adjusted_close": bar.close,
            }
            for bar in value.comparison
        ]
    )
    return DataFeed(prices_df=prices, signals_df=signals)


def _feed_digest(feed: DataFeed) -> str:
    if feed.prices is None or feed.signals is None:
        _deny("RUNTIME_BINDING_MISMATCH", "feed", "final_use")
    return hashlib.sha256(
        _canonical(
            {
                "raw": feed.prices.to_dicts(),
                "signal": feed.signals.to_dicts(),
                "feed_spec": feed.feed_spec,
                "timestamps": feed.timestamps,
                "cursor": feed._idx,
                "context": None if feed.context is None else feed.context.to_dicts(),
            }
        )
    ).hexdigest()


def build_research_engine(bundle: object, *_args, **_kwargs) -> Never:
    """The sole new verified-research build boundary; no real issuer exists yet."""
    if type(bundle) is FrozenDatasetInput:
        snapshot = _reconstruct(bundle, FrozenDatasetInput, "snapshot")
        if snapshot.request.input_kind == "real":
            _deny("REAL_EVIDENCE_DEFERRED", "input_kind", "authority")
        _deny("SYNTHETIC_NOT_TRUSTED", "input_kind", "authority")
    if type(bundle) is not VerifiedDatasetBundle:
        _deny("INVALID_BUNDLE", "bundle", "entry")
    _deny("TRUSTED_ENTRY_BLOCKED", "bundle", "issuance")


class SyntheticTestEngine:
    """Single-use runtime plan. No public Engine/Broker or trusted token is returned.

    Engine construction is lazy: final checks happen before allocation and again
    immediately before Engine.run. Restart means replay from zero, not resumption
    of an arbitrary account, orders, strategy state or an in-flight run.
    """

    def __init__(self, token, source, root, settings, strategy_type, report, software):
        if token is not _TOKEN:
            _deny("INVALID_SYNTHETIC_HANDLE", "handle", "entry")
        self._source, self._root = source, root
        self._settings, self._strategy_type = settings, strategy_type
        self._report, self._software = report, software
        self._started = False

    def __reduce__(self):
        _deny("CHECKPOINT_REQUIRED", "handle", "restore")

    def _check(self):
        try:
            snapshot, report, records = _preflight(
                self._source, self._root, self._settings, self._strategy_type
            )
            if report != self._report or _software() != self._software:
                _deny("RUNTIME_BINDING_MISMATCH", "software_inputs", "entry")
            return snapshot, report, records
        except AttributeError:
            _deny("INVALID_SYNTHETIC_HANDLE", "handle", "entry")

    def checkpoint(self) -> bytes:
        if getattr(self, "_started", True):
            _deny("RESTART_UNAVAILABLE", "checkpoint", "restore")
        snapshot, report, _ = self._check()
        return _canonical(
            {
                "schema_version": CHECKPOINT_SCHEMA,
                "entry_version": ENTRY_VERSION,
                "mode": "synthetic_test",
                "input_root_sha256": snapshot.expected_root_sha256,
                "verification_audit_sha256": report["audit_sha256"],
                "strategy_config_sha256": snapshot.request.strategy_config_sha256,
                "runtime_software_sha256": self._software,
                "state": "not_started_replay_from_zero",
            }
        )

    def run(self):
        if getattr(self, "_started", True):
            _deny("RUN_ALREADY_STARTED", "run", "entry")
        snapshot, report, records = self._check()
        feed = _feed(snapshot, records)
        feed_sha256 = _feed_digest(feed)
        expected_config = self._settings.backtest.merge_feed_spec(feed.feed_spec).to_dict()
        request = snapshot.request

        def context(timestamp, asset, phase):
            if asset != request.symbol:
                _deny("RUNTIME_SCOPE_UNSUPPORTED", "asset", "entry")
            return MarketContext(
                timestamp,
                0,
                phase=phase,
                security_id=request.security_id,
                instrument="equity",
                data_frequency="1m",
                execution_data_mode="raw_execution",
                risk_data_mode="raw_execution",
                signal_data_mode="split_adjusted_signal",
            )

        strategy = self._strategy_type(**decode_json(self._settings.strategy_parameters))
        # Constructor side effects may replace files; recheck before any Broker.
        self._check()
        engine = constrained_engine(
            feed,
            strategy,
            ConstraintController(self._settings.constraints, InMemoryEarningsProvider()),
            context,
            copy.deepcopy(self._settings.backtest),
            corporate_action_provider=_provider(records),
        )
        # Engine construction is not account execution. Check again before on_start,
        # orders, fees, fills, settlement or corporate-action processing can run.
        self._check()
        if _feed_digest(engine.feed) != feed_sha256:
            _deny("RUNTIME_BINDING_MISMATCH", "feed", "final_use")
        if engine.config.to_dict() != expected_config:
            _deny("RUNTIME_BINDING_MISMATCH", "config", "final_use")
        self._started = True
        result = engine.run()
        audit = copy.deepcopy(report)
        audit.pop("audit_sha256")
        audit.update(
            stage="5E-3_isolated_synthetic_entry",
            mode="synthetic_test",
            status="offline_entry_contract_pass",
            code="SYNTHETIC_RUN_COMPLETE",
            phase="complete",
            entry_version=ENTRY_VERSION,
            runtime_software_sha256=self._software,
            derived_feed_sha256=feed_sha256,
            engine_scope="isolated_synthetic_test",
            synthetic_engine_constructed=True,
            restart_policy="not_started_replay_from_zero",
        )
        audit["audit_sha256"] = hashlib.sha256(_canonical(audit)).hexdigest()
        result.metrics["data_provenance_v1"] = audit
        return result


def build_synthetic_test_engine(
    source: FrozenDatasetInput | FrozenDatasetFiles,
    settings: SyntheticRunSettings,
    strategy_type: type[Strategy],
    *,
    reviewed_root_sha256: str,
) -> SyntheticTestEngine:
    """Run 5E-2 afresh and return only an isolated, lazy synthetic plan."""
    settings = _reconstruct(settings, SyntheticRunSettings, "config")
    snapshot, report, _ = _preflight(source, reviewed_root_sha256, settings, strategy_type)
    # Copy in-memory inputs; file declarations retain their independently frozen
    # manifest/root identities and reread only for integrity checks, never feeding paths.
    bound_source = copy.deepcopy(source) if type(source) is FrozenDatasetFiles else snapshot
    return SyntheticTestEngine(
        _TOKEN, bound_source, reviewed_root_sha256, settings, strategy_type, report, _software()
    )


def restore_synthetic_test_engine(
    checkpoint: bytes,
    source: FrozenDatasetInput | FrozenDatasetFiles,
    settings: SyntheticRunSettings,
    strategy_type: type[Strategy],
    *,
    reviewed_root_sha256: str,
) -> SyntheticTestEngine:
    """Revalidate external original roots and config; never adopt a checkpoint's root."""
    payload = decode_json(checkpoint)
    plan = build_synthetic_test_engine(
        source, settings, strategy_type, reviewed_root_sha256=reviewed_root_sha256
    )
    expected = plan.checkpoint()
    if type(payload) is not dict or checkpoint != expected:
        _deny("CHECKPOINT_BINDING_MISMATCH", "checkpoint", "restore")
    return plan
