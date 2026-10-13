"""5C uses invented prices only. No Massive records, credentials, archive or trading."""

import hashlib
import json
from dataclasses import FrozenInstanceError, asdict, replace
from datetime import UTC, date, datetime, timedelta

import pytest

from quant_constraints.calendar import NY, SessionCalendar
from quant_constraints.research.data.contracts import (
    ActionKind,
    CorporateActionRecord,
    IdentityMap,
    IdentityTransition,
    PriceBasis,
    RawBar,
    SecurityIdentity,
    ShareUnit,
    SignalBar,
    SourceProvenance,
    VolumeUnit,
)
from quant_constraints.research.data.raw_validation import (
    FactorDefinition,
    RawOHLCVValidator,
    SplitFact,
    ValidationInput,
    VolumePolicy,
    input_hashes,
)

INGESTED = datetime(2026, 10, 10, tzinfo=UTC)
KNOWN = datetime(2025, 10, 30, 20, tzinfo=UTC)


def source(record, *, provider="synthetic-feed", revision="r1"):
    return SourceProvenance(
        provider=provider,
        dataset_id="invented-not-massive",
        dataset_revision=revision,
        source_partition_id="partition-1",
        source_partition_ref="fixture://invented/partition-1",
        record_id=record,
        record_version=1,
        ingested_at=INGESTED,
        source_uri="fixture://invented",
        source_timezone="America/New_York",
    )


def fact_for(action, *, provider="synthetic-issuer"):
    fact = SplitFact(
        security_id=action.security_id,
        event_id=action.event_id,
        event_version=action.event_version,
        trading_date=action.effective_date,
        new_shares=10,
        old_shares=1,
        published_at=KNOWN,
        source_provider=provider,
        source_ref=action.knowledge_evidence or "fixture://announcement",
        document=b"placeholder",
        document_sha256="",
    )
    return freeze_fact(fact)


def freeze_fact(fact):
    document = fact.payload()
    return replace(fact, document=document, document_sha256=hashlib.sha256(document).hexdigest())


def bind(value):
    return replace(value, expected_hashes=input_hashes(value))


def bundle(*, frequency="1m", volume_policy=VolumePolicy.MULTIPLY_SPLIT_FACTOR):
    # Dates coincide with the independently published NFLX event window; all values,
    # IDs and facts below are invented and MUST NOT be treated as NFLX observations.
    identity = SecurityIdentity(
        security_id="synthetic-A",
        symbol="AAA",
        venue="XNAS",
        currency="USD",
        effective_from=datetime(2020, 1, 1, tzinfo=UTC),
        effective_until=None,
        transition=IdentityTransition.LISTING,
        source=source("identity"),
    )
    action = CorporateActionRecord(
        security_id="synthetic-A",
        event_id="split-1",
        kind=ActionKind.SPLIT,
        effective_date=date(2025, 11, 17),
        historically_known_at=KNOWN,
        knowledge_evidence="fixture://announcement",
        event_version=1,
        source=source("action", provider="synthetic-actions"),
        currency="USD",
        split_ratio=10,
    )
    raws, comparisons = [], []
    for index, day in enumerate((date(2025, 11, 14), date(2025, 11, 17))):
        if frequency == "1m":
            start = SessionCalendar().session(day).market_open
            end = start + timedelta(minutes=1)
        else:
            start = datetime.combine(day, datetime.min.time(), NY)
            end = datetime.combine(day + timedelta(days=1), datetime.min.time(), NY)
        price = 1000 if index == 0 else 100
        raw = RawBar(
            security_id="synthetic-A",
            symbol="AAA",
            venue="XNAS",
            currency="USD",
            product_id="raw",
            bar_start_at=start,
            bar_end_at=end,
            available_at=end,
            open=price,
            high=price * 1.01,
            low=price * 0.99,
            close=price,
            volume=100,
            price_basis=PriceBasis.RAW,
            share_unit=ShareUnit.AS_TRADED,
            volume_unit=VolumeUnit.AS_TRADED_SHARES,
            source=source(f"raw-{index}"),
        )
        ratio = 10 if index == 0 else 1
        signal = SignalBar(
            **(
                asdict(raw)
                | {
                    "source": source(f"comparison-{index}"),
                    "product_id": "comparison",
                    "raw_product_id": "raw",
                    "open": raw.open / ratio,
                    "high": raw.high / ratio,
                    "low": raw.low / ratio,
                    "close": raw.close / ratio,
                    "volume": raw.volume * ratio
                    if volume_policy is VolumePolicy.MULTIPLY_SPLIT_FACTOR
                    else raw.volume,
                    "price_basis": PriceBasis.SPLIT_ADJUSTED,
                    "share_unit": ShareUnit.SPLIT_ADJUSTED,
                    "volume_unit": VolumeUnit.SPLIT_ADJUSTED_SHARES
                    if volume_policy is VolumePolicy.MULTIPLY_SPLIT_FACTOR
                    else VolumeUnit.AS_TRADED_SHARES,
                    "adjustment_version": "synthetic-backward-v1",
                }
            )
        )
        raws.append(raw)
        comparisons.append(signal)
    start = raws[0].bar_start_at
    end = raws[-1].bar_end_at
    definition = FactorDefinition(
        security_id="synthetic-A",
        provider="synthetic-feed",
        dataset_revision="r1",
        raw_product_id="raw",
        comparison_product_id="comparison",
        adjustment_version="synthetic-backward-v1",
        definition_ref="fixture://definition",
        frequency=frequency,
        window_start=start,
        window_end=end,
        adjustment_anchor=end,
        action_coverage_start=start,
        action_coverage_end=end + timedelta(days=1),
        volume_policy=volume_policy,
        input_kind="synthetic",
    )
    return bind(
        ValidationInput(
            raw=tuple(raws),
            comparison=tuple(comparisons),
            actions=(action,),
            identities=IdentityMap((identity,)),
            facts=(fact_for(action),),
            definition=definition,
            expected_hashes=(),
        )
    )


def result(value):
    return RawOHLCVValidator().validate(value)


def reject(value, code):
    report = result(value)
    assert report.status == "reject"
    assert report.code == code
    assert not report.raw_verified and not report.PIT_verified
    assert not report.archive_ready and not report.trusted_entry
    return report


@pytest.mark.parametrize("frequency", ["1m", "1d"])
@pytest.mark.parametrize(
    "volume_policy", [VolumePolicy.AS_TRADED, VolumePolicy.MULTIPLY_SPLIT_FACTOR]
)
def test_correct_forward_split_is_only_synthetic_algorithm_pass(frequency, volume_policy):
    value = bundle(frequency=frequency, volume_policy=volume_policy)
    report = result(value)
    assert report.status == "offline_algorithm_pass"
    assert report.compared_bars == 2
    assert report.input_sha256 and report.source_hashes == value.expected_hashes
    assert not any(
        (report.raw_verified, report.PIT_verified, report.archive_ready, report.trusted_entry)
    )
    assert result(value).as_dict() == report.as_dict()
    assert "trusted_entry_blocked" in report.pending_evidence
    assert "open" not in json.dumps(report.as_dict())
    with pytest.raises(FrozenInstanceError):
        report.raw_verified = True


def test_reverse_split_uses_new_over_old_not_inverse():
    value = bundle()
    action = replace(value.actions[0], split_ratio=0.1)
    fact = freeze_fact(replace(value.facts[0], new_shares=1, old_shares=10))
    signal = replace(value.comparison[0], open=10000, high=10100, low=9900, close=10000, volume=10)
    value = bind(
        replace(value, actions=(action,), facts=(fact,), comparison=(signal, value.comparison[1]))
    )
    assert result(value).status == "offline_algorithm_pass"
    bad = bind(
        replace(
            value,
            comparison=(
                replace(signal, open=100, high=101, low=99, close=100),
                value.comparison[1],
            ),
        )
    )
    reject(bad, "RAW_ADJUSTED_PRICE_CONFLICT")


def test_multiple_events_accumulate_and_include_events_outside_bar_window():
    value = bundle()
    second = replace(
        value.actions[0],
        event_id="split-2",
        effective_date=date(2025, 11, 18),
        split_ratio=2,
        knowledge_evidence="fixture://announcement-2",
        source=source("action-2"),
    )
    fact = freeze_fact(replace(fact_for(second), new_shares=2))
    comparison = tuple(
        replace(
            b,
            open=b.open / 2,
            high=b.high / 2,
            low=b.low / 2,
            close=b.close / 2,
            volume=b.volume * 2,
        )
        for b in value.comparison
    )
    definition = replace(
        value.definition,
        adjustment_anchor=datetime(2025, 11, 18, 21, tzinfo=UTC),
        action_coverage_end=datetime(2025, 11, 19, tzinfo=UTC),
    )
    value = bind(
        replace(
            value,
            definition=definition,
            actions=(value.actions[0], second),
            facts=(value.facts[0], fact),
            comparison=comparison,
        )
    )
    assert result(value).status == "offline_algorithm_pass"
    reject(
        bind(replace(value, actions=value.actions[:1], facts=value.facts[:1])),
        "RAW_ADJUSTED_PRICE_CONFLICT",
    )


def test_adjusted_values_masquerading_as_raw_rejected_even_after_rehash():
    value = bundle()
    raw = replace(value.raw[0], open=100, high=101, low=99, close=100)
    reject(bind(replace(value, raw=(raw, value.raw[1]))), "RAW_ADJUSTED_PRICE_CONFLICT")


@pytest.mark.parametrize(
    "role", ["raw", "comparison", "actions", "identities", "facts", "definition"]
)
def test_every_input_role_is_hash_bound(role):
    value = bundle()
    hashes = tuple((r, "0" * 64 if r == role else h) for r, h in value.expected_hashes)
    reject(replace(value, expected_hashes=hashes), "INPUT_HASH_MISMATCH")


def test_replaced_source_and_normalized_price_are_detected_without_rebinding():
    value = bundle()
    changed = replace(value.raw[0], source=replace(value.raw[0].source, record_version=2))
    reject(replace(value, raw=(changed, value.raw[1])), "INPUT_HASH_MISMATCH")
    changed = replace(value.raw[0], close=1001)
    reject(replace(value, raw=(changed, value.raw[1])), "INPUT_HASH_MISMATCH")


@pytest.mark.parametrize(
    "mutator,code",
    [
        (lambda v: replace(v, actions=(), facts=()), "FACTOR_EVIDENCE_MISSING"),
        (lambda v: replace(v, facts=()), "FACTOR_EVIDENCE_MISSING"),
        (lambda v: replace(v, comparison=v.comparison[:1]), "COMPARISON_COVERAGE_GAP"),
        (lambda v: replace(v, raw=(v.raw[1],), comparison=(v.comparison[1],)), "NO_SPLIT_CONTRAST"),
        (lambda v: replace(v, raw=(v.raw[0], v.raw[0])), "DUPLICATE_BAR"),
        (lambda v: replace(v, comparison=(v.comparison[0], v.comparison[0])), "DUPLICATE_BAR"),
        (
            lambda v: replace(v, facts=(replace(v.facts[0], document=b"changed"),)),
            "SOURCE_HASH_MISMATCH",
        ),
        (
            lambda v: replace(
                v,
                facts=(
                    replace(
                        v.facts[0],
                        document=b"changed",
                        document_sha256=hashlib.sha256(b"changed").hexdigest(),
                    ),
                ),
            ),
            "SOURCE_CONTENT_MISMATCH",
        ),
        (
            lambda v: replace(
                v, facts=(freeze_fact(replace(v.facts[0], source_provider="synthetic-feed")),)
            ),
            "INDEPENDENT_SOURCE_MISSING",
        ),
        (
            lambda v: replace(v, facts=(freeze_fact(replace(v.facts[0], new_shares=4)),)),
            "FACTOR_CONFLICT",
        ),
        (
            lambda v: replace(
                v, facts=(freeze_fact(replace(v.facts[0], trading_date=date(2025, 11, 14))),)
            ),
            "FACTOR_CONFLICT",
        ),
        (
            lambda v: replace(v, facts=(freeze_fact(replace(v.facts[0], event_version=2)),)),
            "FACTOR_CONFLICT",
        ),
        (
            lambda v: replace(
                v, facts=(freeze_fact(replace(v.facts[0], published_at=KNOWN + timedelta(days=1))),)
            ),
            "KNOWLEDGE_TIME_CONFLICT",
        ),
        (
            lambda v: replace(
                v,
                actions=(
                    replace(v.actions[0], historically_known_at=None, knowledge_evidence=None),
                ),
            ),
            "KNOWLEDGE_EVIDENCE_MISSING",
        ),
        (
            lambda v: replace(
                v, actions=(replace(v.actions[0], knowledge_evidence="fixture://wrong"),)
            ),
            "KNOWLEDGE_EVIDENCE_MISSING",
        ),
        (
            lambda v: replace(v, actions=(replace(v.actions[0], cancelled=True),)),
            "ACTION_REVISION_UNSUPPORTED",
        ),
        (
            lambda v: replace(v, actions=(replace(v.actions[0], event_version=2, revision_of=1),)),
            "ACTION_REVISION_UNSUPPORTED",
        ),
        (
            lambda v: replace(v, actions=(replace(v.actions[0], security_id="other"),)),
            "IDENTITY_CONFLICT",
        ),
        (
            lambda v: replace(
                v, definition=replace(v.definition, volume_policy=VolumePolicy.UNKNOWN)
            ),
            "VOLUME_POLICY_UNKNOWN",
        ),
        (
            lambda v: replace(v, definition=replace(v.definition, input_kind="licensed_private")),
            "REAL_DATA_DEFERRED",
        ),
        (
            lambda v: replace(v, definition=replace(v.definition, comparison_product_id="raw")),
            "PRODUCT_COLLISION",
        ),
        (
            lambda v: replace(v, definition=replace(v.definition, frequency="5m")),
            "UNSUPPORTED_FREQUENCY",
        ),
        (
            lambda v: replace(
                v, definition=replace(v.definition, action_coverage_start=v.raw[1].bar_start_at)
            ),
            "FACTOR_COVERAGE_GAP",
        ),
        (
            lambda v: replace(
                v,
                definition=replace(
                    v.definition, action_coverage_end=v.definition.adjustment_anchor
                ),
            ),
            "FACTOR_COVERAGE_GAP",
        ),
        (
            lambda v: replace(v, definition=replace(v.definition, comparison_price_quantum=1)),
            "PRECISION_UNSUPPORTED",
        ),
        (
            lambda v: replace(
                v, comparison=(replace(v.comparison[0], volume=100), v.comparison[1])
            ),
            "RAW_ADJUSTED_VOLUME_CONFLICT",
        ),
        (
            lambda v: replace(
                v,
                comparison=(
                    replace(v.comparison[0], volume_unit=VolumeUnit.AS_TRADED_SHARES),
                    v.comparison[1],
                ),
            ),
            "VOLUME_UNIT_CONFLICT",
        ),
        (
            lambda v: replace(
                v,
                comparison=(replace(v.comparison[0], adjustment_version="wrong"), v.comparison[1]),
            ),
            "PRODUCT_OR_ADJUSTMENT_CONFLICT",
        ),
        (
            lambda v: replace(
                v, comparison=(replace(v.comparison[0], raw_product_id="wrong"), v.comparison[1])
            ),
            "PRODUCT_OR_ADJUSTMENT_CONFLICT",
        ),
        (
            lambda v: replace(
                v,
                comparison=(
                    replace(v.comparison[0], price_basis=PriceBasis.TOTAL_RETURN),
                    v.comparison[1],
                ),
            ),
            "PRODUCT_OR_ADJUSTMENT_CONFLICT",
        ),
        (
            lambda v: replace(
                v, raw=(replace(v.raw[0], source=source("raw-0", revision="r2")), v.raw[1])
            ),
            "PROVENANCE_CONFLICT",
        ),
        (
            lambda v: replace(v, raw=(replace(v.raw[0], security_id="other"), v.raw[1])),
            "IDENTITY_CONFLICT",
        ),
    ],
)
def test_stable_fail_closed_reasons(mutator, code):
    reject(bind(mutator(bundle())), code)


@pytest.mark.parametrize("field", ["open", "high", "low", "close"])
def test_every_price_field_checked(field):
    value = bundle()
    # Preserve OHLC invariants while provoking a mismatch in each tested component.
    changes = {"open": 100.5, "high": 102, "low": 98, "close": 100.5}
    signal = replace(value.comparison[0], **{field: changes[field]})
    reject(
        bind(replace(value, comparison=(signal, value.comparison[1]))),
        "RAW_ADJUSTED_PRICE_CONFLICT",
    )


def test_rounding_tolerance_is_bounded_by_declared_precision():
    value = bundle()
    definition = replace(value.definition, raw_price_quantum=0.01, comparison_price_quantum=0.01)
    signal = replace(value.comparison[0], close=100.005)
    assert (
        result(
            bind(replace(value, definition=definition, comparison=(signal, value.comparison[1])))
        ).status
        == "offline_algorithm_pass"
    )
    reject(
        bind(
            replace(
                value,
                definition=definition,
                comparison=(replace(signal, close=100.006), value.comparison[1]),
            )
        ),
        "RAW_ADJUSTED_PRICE_CONFLICT",
    )


@pytest.mark.parametrize(
    "day,expected_open", [(date(2025, 3, 7), 14), (date(2025, 3, 10), 13), (date(2025, 11, 28), 14)]
)
def test_dst_and_half_day_sessions(day, expected_open):
    value = bundle()
    session = SessionCalendar().session(day)
    assert session.market_open.hour == expected_open
    end = session.market_close
    raw = replace(
        value.raw[0], bar_start_at=end - timedelta(minutes=1), bar_end_at=end, available_at=end
    )
    comparison = replace(
        value.comparison[0], bar_start_at=raw.bar_start_at, bar_end_at=end, available_at=end
    )
    event_day = SessionCalendar().shift(day, 1)
    action = replace(
        value.actions[0],
        effective_date=event_day,
        historically_known_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    fact = freeze_fact(
        replace(value.facts[0], trading_date=event_day, published_at=action.historically_known_at)
    )
    anchor = SessionCalendar().session(event_day).market_close
    definition = replace(
        value.definition,
        window_start=session.market_open,
        window_end=end,
        adjustment_anchor=anchor,
        action_coverage_start=session.market_open,
        action_coverage_end=anchor + timedelta(days=1),
    )
    value = bind(
        replace(
            value,
            raw=(raw,),
            comparison=(comparison,),
            actions=(action,),
            facts=(fact,),
            definition=definition,
        )
    )
    report = result(value)
    assert report.status == "offline_algorithm_pass"
    assert report.missing_rth_minutes == (
        (day.isoformat(), int((end - session.market_open).total_seconds() / 60) - 1),
    )
    raw = replace(
        raw,
        bar_start_at=end,
        bar_end_at=end + timedelta(minutes=1),
        available_at=end + timedelta(minutes=1),
    )
    comparison = replace(
        comparison,
        bar_start_at=raw.bar_start_at,
        bar_end_at=raw.bar_end_at,
        available_at=raw.available_at,
    )
    definition = replace(definition, window_end=raw.bar_end_at)
    reject(
        bind(replace(value, raw=(raw,), comparison=(comparison,), definition=definition)),
        "SESSION_INVALID",
    )


def test_daily_midnight_not_utc_midnight_or_intraday_fill_claim():
    value = bundle(frequency="1d")
    assert value.raw[0].bar_start_at.hour == 5
    raw = replace(value.raw[0], bar_start_at=datetime(2025, 11, 14, 6, tzinfo=UTC))
    comparison = replace(value.comparison[0], bar_start_at=raw.bar_start_at)
    reject(
        bind(replace(value, raw=(raw, value.raw[1]), comparison=(comparison, value.comparison[1]))),
        "DAILY_TIME_BASIS_INVALID",
    )


def test_no_network_file_or_account_side_effects(tmp_path, monkeypatch):
    value = bundle()
    before = asdict(value.raw[0])
    monkeypatch.chdir(tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail("offline validator attempted network I/O")

    monkeypatch.setattr("socket.create_connection", forbidden)
    report = result(value)
    assert report.status == "offline_algorithm_pass"
    reject(
        bind(replace(value, raw=(replace(value.raw[0], close=1001), value.raw[1]))),
        "RAW_ADJUSTED_PRICE_CONFLICT",
    )
    assert asdict(value.raw[0]) == before
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("bad", [None, {}, "verified=True"])
def test_bad_input_is_sanitized_rejection(bad):
    reject(bad, "INVALID_TYPE")


def test_mutated_frozen_record_is_revalidated_not_trusted():
    value = bundle()
    object.__setattr__(value.raw[0], "open", float("nan"))
    report = reject(value, "INVALID_INPUT")
    assert "nan" not in json.dumps(report.as_dict())


@pytest.mark.parametrize("value,code", [(None, "INVALID_TYPE"), ("synthetic", "INVALID_TYPE")])
def test_untyped_definition_rejected(value, code):
    reject(replace(bundle(), definition=value), code)


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"new_shares": 0}, "FACTOR_INVALID"),
        ({"old_shares": True}, "FACTOR_INVALID"),
        ({"event_version": 0}, "FACTOR_INVALID"),
        ({"security_id": "other"}, "FACTOR_CONFLICT"),
        ({"event_id": "other"}, "FACTOR_CONFLICT"),
    ],
)
def test_fact_terms_invalid(changes, code):
    value = bundle()
    fact = freeze_fact(replace(value.facts[0], **changes))
    reject(bind(replace(value, facts=(fact,))), code)


def test_excessive_factor_is_rejected_not_overflowed():
    value = bundle()
    action = replace(value.actions[0], split_ratio=10**7)
    fact = freeze_fact(replace(value.facts[0], new_shares=10**7))
    reject(bind(replace(value, actions=(action,), facts=(fact,))), "FACTOR_INVALID")


@pytest.mark.parametrize(
    "field,bad",
    [
        ("raw_price_quantum", 0),
        ("raw_price_quantum", True),
        ("raw_volume_quantum", 2),
        ("comparison_price_quantum", float("inf")),
    ],
)
def test_invalid_precision_cannot_widen_approval(field, bad):
    value = replace(bundle(), definition=replace(bundle().definition, **{field: bad}))
    report = result(value)
    assert report.status == "reject"
    assert not report.raw_verified


def test_missing_or_duplicate_or_mismatched_event_identity():
    value = bundle()
    reject(
        bind(
            replace(
                value,
                actions=(value.actions[0], value.actions[0]),
                facts=(value.facts[0], value.facts[0]),
            )
        ),
        "FACTOR_CONFLICT",
    )
    other = replace(value.actions[0], event_id="other", source=source("other"))
    fact = freeze_fact(replace(value.facts[0], event_id="missing"))
    reject(
        bind(replace(value, actions=(value.actions[0], other), facts=(value.facts[0], fact))),
        "FACTOR_CONFLICT",
    )
    reject(bind(replace(value, actions=(None,))), "INVALID_TYPE")


def test_non_session_event_and_event_outside_declared_coverage():
    value = bundle()
    action = replace(value.actions[0], effective_date=date(2025, 11, 16))
    fact = freeze_fact(replace(value.facts[0], trading_date=action.effective_date))
    reject(bind(replace(value, actions=(action,), facts=(fact,))), "EVENT_SESSION_INVALID")
    action = replace(action, effective_date=date(2025, 11, 21))
    fact = freeze_fact(replace(fact, trading_date=action.effective_date))
    reject(bind(replace(value, actions=(action,), facts=(fact,))), "FACTOR_COVERAGE_GAP")


def test_late_publication_is_not_backfilled_as_historical_knowledge():
    value = bundle()
    known = datetime(2025, 11, 20, tzinfo=UTC)
    action = replace(value.actions[0], historically_known_at=known)
    fact = freeze_fact(replace(value.facts[0], published_at=known))
    reject(bind(replace(value, actions=(action,), facts=(fact,))), "KNOWLEDGE_TIME_CONFLICT")
    # Retrospective adjustment can use a fact published after the first bar but
    # before the adjustment anchor. It STILL cannot certify strategy-time PIT.
    known = value.definition.adjustment_anchor - timedelta(seconds=1)
    action = replace(action, historically_known_at=known)
    fact = freeze_fact(replace(fact, published_at=known))
    report = result(bind(replace(value, actions=(action,), facts=(fact,))))
    assert report.status == "offline_algorithm_pass" and report.PIT_verified is False


def test_bounds_and_size_fail_closed_before_hashing_large_inputs():
    value = bundle()
    reject(replace(value, raw=value.raw * 7000), "INVALID_TYPE_OR_SIZE")
    reject(replace(value, actions=value.actions * 129), "INVALID_TYPE_OR_SIZE")
    reject(
        replace(value, facts=(replace(value.facts[0], document=b"x" * 16385),)),
        "INVALID_TYPE_OR_SIZE",
    )
    reject(replace(value, facts=(None,)), "INVALID_TYPE_OR_SIZE")
    reject(
        bind(
            replace(
                value,
                definition=replace(
                    value.definition,
                    window_start=value.definition.window_start + timedelta(seconds=1),
                ),
            )
        ),
        "BAR_RANGE_INVALID",
    )
    reject(
        bind(
            replace(
                value, definition=replace(value.definition, window_start=value.raw[0].bar_end_at)
            )
        ),
        "BAR_RANGE_INVALID",
    )


def test_price_window_and_non_trading_bar():
    value = bundle()
    definition = replace(
        value.definition,
        window_start=value.definition.window_start + timedelta(minutes=1),
        action_coverage_start=value.definition.window_start,
    )
    reject(bind(replace(value, definition=definition)), "BAR_RANGE_INVALID")
    start = datetime(2025, 11, 15, 14, 30, tzinfo=UTC)
    raw = replace(
        value.raw[0],
        bar_start_at=start,
        bar_end_at=start + timedelta(minutes=1),
        available_at=start + timedelta(minutes=1),
    )
    signal = replace(
        value.comparison[0],
        bar_start_at=raw.bar_start_at,
        bar_end_at=raw.bar_end_at,
        available_at=raw.available_at,
    )
    reject(bind(replace(value, raw=(raw,), comparison=(signal,))), "SESSION_INVALID")


@pytest.mark.parametrize("volume", [0, 0.25])
def test_zero_and_fractional_volume_are_explicit(volume):
    value = bundle()
    raw = replace(value.raw[0], volume=volume)
    signal = replace(value.comparison[0], volume=volume * 10)
    value = bind(replace(value, raw=(raw, value.raw[1]), comparison=(signal, value.comparison[1])))
    assert result(value).status == "offline_algorithm_pass"
    if volume == 0:
        reject(
            bind(replace(value, comparison=(replace(signal, volume=0.25), value.comparison[1]))),
            "RAW_ADJUSTED_VOLUME_CONFLICT",
        )


def test_per_day_factor_cache_does_not_scan_events_for_each_minute(monkeypatch):
    import quant_constraints.research.data.raw_validation as module

    value = bundle()
    raws, comparisons = [], []
    for raw, signal in zip(value.raw, value.comparison):
        for minute in range(390):
            start = raw.bar_start_at + timedelta(minutes=minute)
            end = start + timedelta(minutes=1)
            raws.append(
                replace(
                    raw,
                    bar_start_at=start,
                    bar_end_at=end,
                    available_at=end,
                    source=source(f"raw-{start.isoformat()}"),
                )
            )
            comparisons.append(
                replace(
                    signal,
                    bar_start_at=start,
                    bar_end_at=end,
                    available_at=end,
                    source=source(f"signal-{start.isoformat()}"),
                )
            )
    end = raws[-1].bar_end_at
    value = bind(
        replace(
            value,
            raw=tuple(raws),
            comparison=tuple(comparisons),
            definition=replace(
                value.definition,
                window_end=end,
                adjustment_anchor=end,
                action_coverage_end=end + timedelta(days=1),
            ),
        )
    )
    scans = []
    original = module._events

    class Counted:
        def __init__(self, entries):
            self.entries = entries

        def __iter__(self):
            scans.append(1)
            return iter(self.entries)

    monkeypatch.setattr(
        module, "_events", lambda value, calendar: Counted(original(value, calendar))
    )
    report = result(value)
    assert report.status == "offline_algorithm_pass" and report.compared_bars == 780
    assert len(scans) == 2  # two sessions, NOT 780 per-bar event scans
    assert report.missing_rth_minutes == (("2025-11-14", 0), ("2025-11-17", 0))
