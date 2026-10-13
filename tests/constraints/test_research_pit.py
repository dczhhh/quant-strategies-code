"""5D-1 synthetic clocks/revisions and the unchanged PR #4 ledger protocol."""

import copy
import hashlib
import json
import pickle
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, timedelta

import pytest
from test_adapter import at
from test_corporate_actions import build, held

from quant_constraints.corporate_actions import CorporateActionProcessor
from quant_constraints.research.data.contracts import (
    ActionKind,
    CorporateActionRecord,
    CreditEvidence,
    IdentityTransition,
    SecurityIdentity,
    SourceProvenance,
)
from quant_constraints.research.data.pit import (
    ACTION_KINDS,
    MAX_DOCUMENT_BYTES,
    MAX_REVISIONS,
    PIT_VERSION,
    PITCoverage,
    PITDataset,
    PITError,
    PITEventRevision,
    PITIdentityRevision,
    SnapshotState,
    SyntheticPITProvider,
    evidence_payload,
    pit_input_sha256,
)


def source(**changes):
    values = {
        "provider": "synthetic-fixture",
        "dataset_id": "pit-contract-fixture",
        "dataset_revision": "fixture-v1",
        "source_partition_id": "partition",
        "source_partition_ref": "synthetic:partition",
        "record_id": "record",
        "record_version": 1,
        "ingested_at": at("2026-10-20"),
        "source_uri": "synthetic:document",
        "source_timezone": "America/New_York",
    }
    return SourceProvenance(**(values | changes))


def document(value):
    payload = evidence_payload(value)
    return replace(value, document=payload, document_sha256=hashlib.sha256(payload).hexdigest())


def event(kind=ActionKind.SPLIT, known=at("2026-10-01"), **changes):
    record_values = {
        "security_id": "stable-A",
        "event_id": "split",
        "kind": kind,
        "effective_date": date(2026, 10, 12),
        "historically_known_at": known,
        "knowledge_evidence": "synthetic:knowledge" if known is not None else None,
        "event_version": 1,
        "source": source(),
        "currency": "USD",
    }
    if kind is ActionKind.SPLIT:
        record_values["split_ratio"] = 2.0
    elif kind is ActionKind.CASH_DIVIDEND:
        record_values.update(
            event_id="dividend",
            dividend_per_share=1.0,
            ex_date=date(2026, 10, 12),
            payable_date=date(2026, 10, 16),
        )
    else:
        record_values.update(
            event_id="credit",
            effective_date=date(2026, 10, 16),
            credit_date=date(2026, 10, 16),
            parent_event_id="dividend",
            credited_net=20.0,
            credit_evidence=CreditEvidence.SIMULATED_ASSUMPTION,
            credit_evidence_ref="synthetic:explicit-credit-assumption",
        )
    published = changes.pop("published_at", known)
    provider = changes.pop("provider_available_at", None)
    record = CorporateActionRecord(**(record_values | changes))
    return document(
        PITEventRevision(
            record=record,
            published_at=published,
            provider_available_at=provider,
            document=b"",
            document_sha256="",
        )
    )


def coverage(**changes):
    values = {
        "security_id": "stable-A",
        "coverage_id": "coverage",
        "version": 1,
        "revision_of": None,
        "covered_from": date(2026, 10, 1),
        "covered_until": date(2026, 10, 31),
        "action_kinds": ACTION_KINDS,
        "historically_known_at": at("2026-10-01"),
        "published_at": at("2026-10-01"),
        "provider_available_at": None,
        "source": source(),
        "point_in_time": True,
        "missing": False,
        "cancelled": False,
        "document": b"",
        "document_sha256": "",
    }
    return document(PITCoverage(**(values | changes)))


def identity(**changes):
    wrapper = {
        "identity_id": "identity-A",
        "version": 1,
        "revision_of": None,
        "historically_known_at": at("2026-10-01"),
        "published_at": at("2026-10-01"),
        "provider_available_at": None,
        "cancelled": False,
        "document": b"",
        "document_sha256": "",
    }
    for key in tuple(changes):
        if key in wrapper:
            wrapper[key] = changes.pop(key)
    record = SecurityIdentity(
        **(
            {
                "security_id": "stable-A",
                "symbol": "A",
                "venue": "XNYS",
                "currency": "USD",
                "effective_from": at("2026-01-01", clock="00:00"),
                "effective_until": None,
                "transition": IdentityTransition.LISTING,
                "source": source(),
            }
            | changes
        )
    )
    return document(PITIdentityRevision(record=record, **wrapper))


def data(events=(), identities=(), proofs=None, **changes):
    dataset = PITDataset(
        events=tuple(events),
        identities=tuple(identities),
        coverage=tuple(proofs if proofs is not None else [coverage()]),
        input_kind="synthetic",
        expected_sha256="",
    )
    dataset = replace(dataset, **changes)
    return replace(dataset, expected_sha256=pit_input_sha256(dataset))


def provider(events=(), identities=(), proofs=None):
    return SyntheticPITProvider(data(events, identities, proofs))


def assert_error(code, call):
    with pytest.raises(PITError) as caught:
        call()
    assert caught.value.code == code
    assert str(caught.value) == f"{caught.value.code}:{caught.value.field}"


def tick(broker, timestamp=None, price=100):
    # This dataset covers A only; do not implicitly submit the seven-asset fixture feed.
    broker._update_time(
        timestamp or at(), {"A": price}, {"A": price}, {"A": price}, {"A": price}, {"A": 10000}, {}
    )


def test_four_snapshot_states_and_safe_audit():
    no_events = provider().inspect("stable-A", at())
    assert no_events.state is SnapshotState.PROVEN_NO_EVENTS
    valid = provider([event()]).inspect("stable-A", at())
    assert valid.state is SnapshotState.EVENTS
    unknown = provider(proofs=[]).inspect("stable-A", at())
    assert (
        unknown.state is SnapshotState.UNKNOWN and not unknown.events and unknown.coverage is None
    )
    conflicting = event(split_ratio=10)
    conflict = provider([event(), conflicting]).inspect("stable-A", at())
    assert conflict.state is SnapshotState.CONFLICT and conflict.code == "PIT_REVISION_CONFLICT"
    for result in (no_events, valid, unknown, conflict):
        report = result.audit_dict()
        assert all(
            report[key] is False
            for key in ("raw_verified", "PIT_verified", "archive_ready", "trusted_entry")
        )
        assert "stable-A" not in json.dumps(report) and "split_ratio" not in json.dumps(report)
        assert result.asof.tzinfo is UTC and report["provider_version"] == PIT_VERSION
    assert valid.audit_dict()["status"] == "synthetic_pit_contract_pass"
    assert unknown.audit_dict()["status"] == "rejected"


def test_t0_t1_t2_revisions_and_tombstone_never_rewrite_past():
    first = event()
    second = event(known=at("2026-10-09"), event_version=2, revision_of=1, split_ratio=10)
    tombstone = event(
        known=at("2026-10-10"), event_version=3, revision_of=2, split_ratio=10, cancelled=True
    )
    supplied = provider([tombstone, first, second])
    assert supplied.snapshot("stable-A", at("2026-10-08"))[1][0].split_ratio == 2
    assert supplied.snapshot("stable-A", at("2026-10-09"))[1][0].split_ratio == 10
    result = supplied.inspect("stable-A", at("2026-10-10"))
    assert result.state is SnapshotState.PROVEN_NO_EVENTS
    assert result.events[0].cancelled and result.events[0].version == 3
    assert supplied.snapshot("stable-A", at("2026-10-08"))[1][0].version == 1


def test_future_conflicting_same_version_does_not_poison_old_snapshot():
    first = event()
    a = event(known=at("2026-10-09"), event_version=2, revision_of=1, split_ratio=4)
    b = event(known=at("2026-10-09"), event_version=2, revision_of=1, split_ratio=10)
    supplied = provider([b, first, a])
    assert supplied.inspect("stable-A", at("2026-10-08")).state is SnapshotState.EVENTS
    assert supplied.inspect("stable-A", at("2026-10-09")).code == "PIT_REVISION_CONFLICT"
    assert_error("PIT_REVISION_CONFLICT", lambda: supplied.snapshot("stable-A", at("2026-10-09")))


def test_duplicate_exact_revision_is_deduplicated_and_order_independent():
    a, b = event(), event(event_id="other", split_ratio=10)
    first, second = data([a, b, a]), data([b, a, a])
    assert first.expected_sha256 == second.expected_sha256
    assert provider([a, b, a]).snapshot("stable-A", at()) == provider([b, a, a]).snapshot(
        "stable-A", at()
    )
    assert len(provider([a, a]).snapshot("stable-A", at())[1]) == 1


@pytest.mark.parametrize("what", ["events", "coverage", "identities"])
def test_visible_version_gap_is_rejected(what):
    if what == "events":
        supplied = provider([event(event_version=2, revision_of=1)])
        assert supplied.inspect("stable-A", at()).code == "PIT_REVISION_GAP"
    elif what == "coverage":
        supplied = provider(proofs=[coverage(version=2, revision_of=1)])
        assert supplied.inspect("stable-A", at()).code == "PIT_REVISION_GAP"
    else:
        supplied = provider(identities=[identity(version=2, revision_of=1)])
        assert_error("PIT_REVISION_GAP", lambda: supplied.resolve_asof("A", "XNYS", at(), at()))


@pytest.mark.parametrize("new_clock", [at("2026-10-01"), at("2026-09-30")])
def test_equal_or_reversed_revision_knowledge_clock_rejected(new_clock):
    second = event(known=new_clock, event_version=2, revision_of=1)
    assert provider([event(), second]).inspect("stable-A", at()).code == "PIT_REVISION_CLOCK"


def test_revision_cannot_change_event_kind():
    second = event(
        ActionKind.CASH_DIVIDEND,
        known=at("2026-10-09"),
        event_id="split",
        event_version=2,
        revision_of=1,
    )
    assert (
        provider([event(), second]).inspect("stable-A", at("2026-10-09")).code
        == "PIT_REVISION_KIND"
    )


@pytest.mark.parametrize(
    "what", ["event-known", "event-published", "coverage-known", "identity-known"]
)
def test_unknown_public_clock_is_not_backfilled_from_ingestion(what):
    if what.startswith("event"):
        value = event(known=None) if what == "event-known" else event(published_at=None)
        result = provider([value]).inspect("stable-A", at())
        assert result.code == "PIT_KNOWLEDGE_UNKNOWN" and result.state is SnapshotState.UNKNOWN
    elif what == "coverage-known":
        assert (
            provider(proofs=[coverage(historically_known_at=None)]).inspect("stable-A", at()).state
            is SnapshotState.UNKNOWN
        )
    else:
        supplied = provider(identities=[identity(historically_known_at=None)])
        assert_error(
            "PIT_KNOWLEDGE_UNKNOWN", lambda: supplied.resolve_asof("A", "XNYS", at(), at())
        )


def test_provider_delay_and_publication_boundaries_use_exact_instant():
    value = event(provider_available_at=at("2026-10-08"))
    supplied = provider([value])
    before = at("2026-10-08") - timedelta(microseconds=1)
    assert supplied.inspect("stable-A", before).state is SnapshotState.PROVEN_NO_EVENTS
    assert supplied.snapshot("stable-A", at("2026-10-08"))[1][0].available_at == at("2026-10-08")
    assert value.record.source.ingested_at == at("2026-10-20")  # Not the historical clock.


@pytest.mark.parametrize(
    "changes",
    [
        {"historically_known_at": at("2026-10-21"), "published_at": at("2026-10-01")},
        {"published_at": at("2026-10-21")},
        {"provider_available_at": at("2026-10-21")},
        {"published_at": at("2026-10-02")},
        {"provider_available_at": at("2026-09-30")},
    ],
)
def test_invalid_clock_orders_rejected(changes):
    value = document(replace(coverage(), **changes))
    assert_error("PIT_TIME_ORDER", lambda: SyntheticPITProvider(data(proofs=[value])))


@pytest.mark.parametrize(
    "changes",
    [
        {"missing": True},
        {"point_in_time": False},
        {"cancelled": True},
        {"action_kinds": (ActionKind.SPLIT,)},
        {"covered_from": date(2026, 10, 10)},
        {"covered_until": date(2026, 10, 7)},
        {"historically_known_at": at("2026-10-10")},
    ],
)
def test_coverage_gaps_are_not_empty_event_proof(changes):
    supplied = provider(proofs=[coverage(**changes)])
    assert supplied.inspect("stable-A", at()).state is SnapshotState.UNKNOWN
    assert_error("PIT_COVERAGE_UNKNOWN", lambda: supplied.snapshot("stable-A", at()))


def test_coverage_revocation_does_not_fall_back_to_old_broad_proof():
    missing = coverage(
        version=2,
        revision_of=1,
        historically_known_at=at("2026-10-09"),
        published_at=at("2026-10-09"),
        missing=True,
    )
    supplied = provider(proofs=[coverage(), missing])
    assert supplied.inspect("stable-A", at("2026-10-08")).state is SnapshotState.PROVEN_NO_EVENTS
    assert supplied.inspect("stable-A", at("2026-10-09")).state is SnapshotState.UNKNOWN


def test_different_overlapping_coverage_sources_conflict():
    assert (
        provider(proofs=[coverage(), coverage(coverage_id="other")]).inspect("stable-A", at()).code
        == "PIT_COVERAGE_CONFLICT"
    )


def monthly_proofs(**november_changes):
    return (
        coverage(),
        coverage(
            coverage_id="november",
            covered_from=date(2026, 11, 1),
            covered_until=date(2026, 11, 30),
            **november_changes,
        ),
    )


@pytest.mark.parametrize("day", ["2026-10-31", "2026-11-01", "2026-11-30"])
def test_adjacent_months_bind_old_split_and_keep_inclusive_boundaries(day):
    split = event()
    proofs = monthly_proofs()
    supplied = provider([split], proofs=reversed(proofs))
    snapshot = supplied.inspect("stable-A", at(day, "00:00"))
    assert snapshot.state is SnapshotState.EVENTS
    assert snapshot.events[0].event_id == "split"
    assert snapshot.coverage.covered_from == date(2026, 10, 1)
    assert snapshot.coverage.covered_until == date(2026, 11, 30)
    assert set(snapshot.source_hashes) == {v.document_sha256 for v in (*proofs, split)}
    assert snapshot == provider([split], proofs=proofs).inspect("stable-A", at(day, "00:00"))
    assert supplied.inspect("stable-A", at("2026-12-01", "00:00")).state is SnapshotState.UNKNOWN


def test_old_month_unprocessed_split_reaches_processor_and_is_applied_once():
    supplied = provider([event()], proofs=monthly_proofs())
    broker, _, _, _, _ = build()
    broker.corporate_action_processor = CorporateActionProcessor(broker, supplied)
    held(broker)
    tick(broker)
    tick(broker, at("2026-11-02"), price=50)
    assert broker.positions["A"].quantity == 40
    tick(broker, at("2026-11-03"), price=50)
    assert broker.positions["A"].quantity == 40
    assert len(broker.corporate_action_processor.state["records"]) == 1


def cross_month_dividend_credit():
    return (
        event(
            ActionKind.CASH_DIVIDEND,
            effective_date=date(2026, 10, 30),
            ex_date=date(2026, 10, 30),
            payable_date=date(2026, 11, 2),
        ),
        event(
            ActionKind.CASH_CREDIT,
            effective_date=date(2026, 11, 2),
            credit_date=date(2026, 11, 2),
        ),
    )


def test_cross_month_ex_date_credit_retains_parent_and_credits_once():
    supplied = provider(cross_month_dividend_credit(), proofs=monthly_proofs())
    broker, _, _, _, _ = build()
    broker.corporate_action_processor = CorporateActionProcessor(broker, supplied)
    held(broker)
    tick(broker, at("2026-10-29"))
    cash = broker.cash
    tick(broker, at("2026-10-30"), price=99)
    assert broker.cash == cash and sum(broker.account._receivables.values()) == 20
    tick(broker, at("2026-11-02"), price=99)
    assert broker.cash == cash + 20 and not broker.account._receivables
    tick(broker, at("2026-11-03"), price=99)
    assert broker.cash == cash + 20
    assert {e.event_id for e in supplied.snapshot("stable-A", at("2026-11-03"))[1]} == {
        "dividend",
        "credit",
    }


def test_gap_between_parent_and_credit_rejects_even_with_both_event_proofs():
    october, november = monthly_proofs()
    october = document(replace(october, covered_until=date(2026, 10, 30)))
    supplied = provider(cross_month_dividend_credit(), proofs=[october, november])
    assert supplied.inspect("stable-A", at("2026-11-02")).code == "PIT_CREDIT_COVERAGE"


def test_gap_between_bars_rejected_before_existing_ledger_mutation():
    october, november = monthly_proofs()
    october = document(replace(october, covered_until=date(2026, 10, 30)))
    supplied = provider([event()], proofs=[october, november])
    # Both independent event/current proofs remain visible; the gap is not filled.
    snapshot = supplied.inspect("stable-A", at("2026-11-02"))
    assert snapshot.events[0].event_id == "split"
    assert snapshot.coverage.covered_from == date(2026, 11, 1)
    broker, _, _, _, _ = build()
    broker.corporate_action_processor = CorporateActionProcessor(broker, supplied)
    held(broker)
    tick(broker)
    checkpoint = copy.deepcopy(broker.corporate_action_processor.state)
    with pytest.raises(ValueError, match="Missing point-in-time corporate action coverage"):
        tick(broker, at("2026-11-02"), price=50)
    assert broker.corporate_action_processor.state == checkpoint
    assert broker.positions["A"].quantity == 20 and broker.cash == 8000


@pytest.mark.parametrize(
    "change",
    [
        {"missing": True},
        {"cancelled": True},
        {"point_in_time": False},
        {"action_kinds": (ActionKind.SPLIT,)},
    ],
)
def test_incomplete_adjacent_month_is_not_stitched_into_valid_coverage(change):
    supplied = provider(proofs=monthly_proofs(**change))
    assert supplied.snapshot("stable-A", at("2026-10-30"))[0].covered_until == date(2026, 10, 31)
    assert supplied.inspect("stable-A", at("2026-11-02")).state is SnapshotState.UNKNOWN


def test_overlap_at_inclusive_boundary_and_inside_connected_component_rejects():
    october, november = monthly_proofs()
    overlap = document(replace(november, covered_from=date(2026, 10, 31)))
    supplied = provider(proofs=[october, overlap])
    for day in ("2026-10-12", "2026-10-31", "2026-11-02"):
        assert supplied.inspect("stable-A", at(day)).code == "PIT_COVERAGE_CONFLICT"


def test_new_month_tombstone_and_coverage_revision_do_not_replace_old_asof():
    first = event()
    cancelled = event(
        known=at("2026-11-03"),
        event_version=2,
        revision_of=1,
        cancelled=True,
        source=source(ingested_at=at("2026-12-31")),
    )
    october, november = monthly_proofs()
    revoked = document(
        replace(
            november,
            version=2,
            revision_of=1,
            historically_known_at=at("2026-11-04"),
            published_at=at("2026-11-04"),
            cancelled=True,
            source=source(ingested_at=at("2026-12-31")),
        )
    )
    supplied = provider([first, cancelled], proofs=[october, november, revoked])
    old = supplied.snapshot("stable-A", at("2026-10-12"))
    assert supplied.snapshot("stable-A", at("2026-11-03"))[1][0].cancelled
    assert supplied.inspect("stable-A", at("2026-11-04")).state is SnapshotState.UNKNOWN
    assert supplied.snapshot("stable-A", at("2026-10-12")) == old


@pytest.mark.parametrize("effective", [date(2026, 9, 30), date(2026, 11, 1)])
def test_visible_events_outside_bound_proof_rejected(effective):
    assert (
        provider([event(effective_date=effective)]).inspect("stable-A", at()).code
        == "PIT_EVENT_OUTSIDE_COVERAGE"
    )


@pytest.mark.parametrize("field", ["input_kind", "schema_version", "expected_sha256"])
def test_dataset_real_data_unsupported_schema_and_swapped_hash_rejected(field):
    value = replace(
        data([event()]), **{field: "licensed_private" if field == "input_kind" else "wrong"}
    )
    code = {
        "input_kind": "REAL_DATA_DEFERRED",
        "schema_version": "UNSUPPORTED_PIT_SCHEMA",
        "expected_sha256": "PIT_INPUT_HASH",
    }[field]
    assert_error(code, lambda: SyntheticPITProvider(value))


@pytest.mark.parametrize("what", ["event", "identity", "coverage"])
@pytest.mark.parametrize(
    "mutation,code",
    [
        ("bytes", "PIT_DOCUMENT_HASH"),
        ("rehashed", "PIT_DOCUMENT_TERMS"),
        ("oversize", "PIT_DOCUMENT_BOUNDS"),
        ("empty", "PIT_DOCUMENT_BOUNDS"),
        ("schema", "UNSUPPORTED_PIT_SCHEMA"),
    ],
)
def test_source_bytes_terms_and_versions_are_bound(what, mutation, code):
    value = {"event": event, "identity": identity, "coverage": coverage}[what]()
    if mutation == "schema":
        value = document(replace(value, schema_version="research_pit_synthetic_v0"))
    else:
        body = {
            "bytes": b"substitution",
            "rehashed": b"substitution",
            "oversize": b"x" * (MAX_DOCUMENT_BYTES + 1),
            "empty": b"",
        }[mutation]
        value = replace(value, document=body)
        if mutation == "rehashed":
            value = replace(value, document_sha256=hashlib.sha256(body).hexdigest())
    args = (
        {"events": [value]}
        if what == "event"
        else ({"identities": [value]} if what == "identity" else {"proofs": [value]})
    )
    assert_error(code, lambda: SyntheticPITProvider(data(**args)))


def test_changed_economic_term_with_old_document_cannot_be_resealed():
    value = event()
    swapped = replace(value, record=replace(value.record, split_ratio=10))
    assert_error("PIT_DOCUMENT_TERMS", lambda: SyntheticPITProvider(data([swapped])))
    # Internally consistent resealing remains a synthetic contract, not real certification.
    result = provider([document(swapped)]).inspect("stable-A", at())
    assert result.audit_dict()["PIT_verified"] is False


@pytest.mark.parametrize("name", ["events", "identities", "coverage"])
def test_bounded_and_immutable_input_collections(name):
    value = {"events": event, "identities": identity, "coverage": coverage}[name]()
    for bad in ([value], tuple([value] * (MAX_REVISIONS + 1)), ("bad-type",)):
        dataset = replace(data(), **{name: bad})
        assert_error(
            "PIT_INPUT_BOUNDS_OR_TYPE", lambda dataset=dataset: SyntheticPITProvider(dataset)
        )


def test_query_timestamp_types_and_diagnostics():
    supplied = provider(identities=[identity()])
    assert_error("INVALID_TYPE", lambda: SyntheticPITProvider(None))
    assert_error("NAIVE_TIMESTAMP", lambda: supplied.inspect("stable-A", datetime(2026, 10, 8)))
    assert_error("INVALID_VALUE", lambda: supplied.inspect(" stable-A", at()))
    assert_error(
        "NAIVE_TIMESTAMP", lambda: supplied.resolve_asof("A", "XNYS", datetime(2026, 10, 8), at())
    )
    assert supplied.inspect("different-security", at()).state is SnapshotState.UNKNOWN


def renamed_identities():
    old = identity()
    known = at("2026-10-09")
    end = at("2026-10-12", clock="00:00")
    closed = identity(
        version=2,
        revision_of=1,
        historically_known_at=known,
        published_at=known,
        effective_until=end,
    )
    renamed = identity(
        identity_id="identity-B",
        symbol="B",
        transition=IdentityTransition.RENAME,
        effective_from=end,
        historically_known_at=known,
        published_at=known,
    )
    return old, closed, renamed


def test_rename_uses_knowledge_versions_and_half_open_effective_intervals():
    supplied = provider(identities=renamed_identities())
    boundary = at("2026-10-12", clock="00:00")
    assert supplied.resolve_asof("A", "XNYS", boundary, at("2026-10-08")).symbol == "A"
    assert_error(
        "IDENTITY_MISSING", lambda: supplied.resolve_asof("B", "XNYS", boundary, at("2026-10-08"))
    )
    assert (
        supplied.resolve_asof(
            "A", "XNYS", boundary - timedelta(microseconds=1), at("2026-10-09")
        ).security_id
        == "stable-A"
    )
    assert supplied.resolve_asof("B", "XNYS", boundary, at("2026-10-09")).security_id == "stable-A"
    assert_error(
        "IDENTITY_MISSING", lambda: supplied.resolve_asof("A", "XNYS", boundary, at("2026-10-09"))
    )


def test_ticker_reuse_and_identity_id_owner_change_rejected():
    assert_error(
        "UNSUPPORTED_IDENTITY",
        lambda: provider(
            identities=[
                identity(),
                identity(security_id="other-security", effective_from=at("2026-10-30")),
            ]
        ),
    )
    assert_error(
        "PIT_IDENTITY_OWNER",
        lambda: provider(
            identities=[identity(), identity(security_id="other-security", symbol="B")]
        ),
    )


def test_identity_conflict_unknown_mapping_and_cancellation():
    supplied = provider(identities=[identity(), identity(identity_id="other")])
    assert_error("IDENTITY_CONFLICT", lambda: supplied.resolve_asof("A", "XNYS", at(), at()))
    cancelled = identity(
        version=2,
        revision_of=1,
        historically_known_at=at("2026-10-09"),
        published_at=at("2026-10-09"),
        cancelled=True,
    )
    supplied = provider(identities=[identity(), cancelled])
    assert supplied.resolve_asof("A", "XNYS", at(), at("2026-10-08")).symbol == "A"
    assert_error(
        "IDENTITY_MISSING", lambda: supplied.resolve_asof("A", "XNYS", at(), at("2026-10-09"))
    )
    assert_error("IDENTITY_MISSING", lambda: supplied.resolve_asof("A", "OTHER", at(), at()))


def test_index_does_not_read_unrelated_security_revisions(monkeypatch):
    from quant_constraints.research.data import pit

    supplied = provider(
        [event(), event(security_id="stable-B", event_id="b")],
        [identity(), identity(security_id="stable-B", symbol="B", identity_id="b")],
        [coverage(), coverage(security_id="stable-B", coverage_id="b")],
    )
    original, seen = pit._latest, []

    def tracked(values, asof):
        seen.extend(pit._key(value)[0] for value in values)
        return original(values, asof)

    monkeypatch.setattr(pit, "_latest", tracked)
    supplied.snapshot("stable-A", at())
    supplied.resolve_asof("A", "XNYS", at(), at())
    assert seen and set(seen) == {"stable-A"}


def test_canonical_checkpoint_and_original_mutation_isolation():
    original = event()
    id_record = identity()
    supplied = provider([original], [id_record])
    baseline = supplied.snapshot("stable-A", at())
    object.__setattr__(original.record, "split_ratio", 999)
    object.__setattr__(id_record.record, "symbol", "tampered")
    assert supplied.snapshot("stable-A", at()) == baseline
    returned = supplied.resolve_asof("A", "XNYS", at(), at())
    object.__setattr__(returned, "symbol", "tampered")
    assert supplied.resolve_asof("A", "XNYS", at(), at()).symbol == "A"
    for restored in (copy.deepcopy(supplied), pickle.loads(pickle.dumps(supplied))):
        assert restored.snapshot("stable-A", at()) == baseline
        assert (
            restored.inspect("stable-A", at()).audit_dict()
            == supplied.inspect("stable-A", at()).audit_dict()
        )


@pytest.mark.parametrize("ratio", [2, 10, 0.1])
def test_adapter_split_only_once_through_existing_ledger(ratio):
    supplied = provider([event(split_ratio=ratio)])
    broker, _, _, _, _ = build()
    broker.corporate_action_processor = CorporateActionProcessor(broker, supplied)
    held(broker)
    tick(broker)
    cash, fills = broker.cash, len(broker.fills)
    tick(broker, at("2026-10-12"), price=100 / ratio)
    assert broker.positions["A"].quantity == pytest.approx(20 * ratio)
    tick(broker, at("2026-10-13"), price=100 / ratio)
    assert broker.positions["A"].quantity == pytest.approx(20 * ratio)
    assert broker.cash == cash and len(broker.fills) == fills and not broker.trades


def test_payable_date_never_creates_credit_and_explicit_credit_is_once():
    dividend = event(ActionKind.CASH_DIVIDEND)
    broker, _, _, _, _ = build()
    broker.corporate_action_processor = CorporateActionProcessor(broker, provider([dividend]))
    held(broker)
    tick(broker)
    cash = broker.cash
    tick(broker, at("2026-10-12"))
    assert broker.cash == cash and sum(broker.account._receivables.values()) == 20
    tick(broker, at("2026-10-16"))
    assert broker.cash == cash  # Payable metadata alone does not authorize cash.
    credit = event(ActionKind.CASH_CREDIT, known=at("2026-10-16"))
    broker.corporate_action_processor.provider = provider([dividend, credit])
    tick(broker, at("2026-10-16", clock="10:31"))
    assert broker.cash == cash + 20 and sum(broker.account._receivables.values()) == 0
    tick(broker, at("2026-10-19"))
    assert broker.cash == cash + 20 and not broker.fills and not broker.trades


def test_announced_simulated_credit_not_emitted_until_credit_date():
    supplied = provider([event(ActionKind.CASH_DIVIDEND), event(ActionKind.CASH_CREDIT)])
    assert [e.kind for e in supplied.snapshot("stable-A", at())[1]] == ["CASH_DIVIDEND"]
    assert len(supplied.snapshot("stable-A", at("2026-10-16"))[1]) == 2


@pytest.mark.parametrize("mutation", ["missing", "cancelled", "kind", "date", "duplicate"])
def test_bad_credit_parent_and_duplicate_credits_rejected(mutation):
    dividend = event(ActionKind.CASH_DIVIDEND)
    credit = event(ActionKind.CASH_CREDIT, known=at("2026-10-16"))
    events = [dividend, credit]
    if mutation == "missing":
        events = [credit]
    elif mutation == "cancelled":
        events[0] = event(ActionKind.CASH_DIVIDEND, cancelled=True)
    elif mutation == "kind":
        events[0] = event(event_id="dividend")
    elif mutation == "date":
        events[0] = event(
            ActionKind.CASH_DIVIDEND,
            effective_date=date(2026, 10, 19),
            ex_date=date(2026, 10, 19),
            payable_date=date(2026, 10, 20),
        )
    else:
        events.append(
            event(ActionKind.CASH_CREDIT, known=at("2026-10-16"), event_id="second-credit")
        )
    expected = "PIT_DUPLICATE_CREDIT" if mutation == "duplicate" else "PIT_CREDIT_PARENT"
    assert provider(events).inspect("stable-A", at("2026-10-16")).code == expected


def test_synthetic_documents_cannot_claim_broker_confirmation():
    value = event(
        ActionKind.CASH_CREDIT,
        known=at("2026-10-16"),
        credit_evidence=CreditEvidence.BROKER_CONFIRMATION,
    )
    assert_error("SYNTHETIC_CREDIT_REQUIRED", lambda: provider([value]))


@pytest.mark.parametrize("what", ["coverage", "conflict", "unknown"])
def test_rejection_before_bar_cash_position_or_orders_change(what):
    supplied = (
        provider(proofs=[])
        if what == "coverage"
        else provider(
            [event(), event(split_ratio=10)] if what == "conflict" else [event(known=None)]
        )
    )
    broker, _, _, _, _ = build()
    broker.corporate_action_processor = CorporateActionProcessor(broker, supplied)
    held(broker)

    def financial_state():
        return (
            broker.cash,
            broker.settled_cash,
            copy.deepcopy(vars(broker.positions["A"])),
            copy.deepcopy(broker.account._receivables),
            copy.deepcopy(broker.corporate_action_processor.state),
            copy.deepcopy(broker.orders),
            copy.deepcopy(broker.fills),
        )

    before = financial_state()
    with pytest.raises(PITError):
        tick(broker)
    assert before == financial_state()


def test_future_economic_revision_requires_existing_processor_reconciliation():
    first = event()
    later = event(known=at("2026-10-13"), event_version=2, revision_of=1, split_ratio=10)
    broker, _, _, _, _ = build()
    broker.corporate_action_processor = CorporateActionProcessor(broker, provider([first, later]))
    held(broker)
    tick(broker)
    tick(broker, at("2026-10-12"), price=50)
    before = broker.positions["A"].quantity, broker.cash
    with pytest.raises(ValueError, match="Applied corporate action revised"):
        tick(broker, at("2026-10-13"), price=50)
    assert (broker.positions["A"].quantity, broker.cash) == before


@pytest.mark.parametrize("effective", [date(2026, 10, 10), date(2026, 10, 12)])
def test_weekend_effective_or_missing_bar_uses_existing_session_processor(effective):
    broker, _, _, _, _ = build()
    broker.corporate_action_processor = CorporateActionProcessor(
        broker, provider([event(effective_date=effective)])
    )
    held(broker)
    tick(broker)
    tick(broker, at("2026-10-13"), price=50)
    assert broker.positions["A"].quantity == 40 and not broker.fills


@pytest.mark.parametrize("local_day,utc_hour", [("2026-03-06", 15), ("2026-03-09", 14)])
def test_asof_uses_absolute_clocks_across_new_york_DST(local_day, utc_hour):
    known = at(local_day)
    value = event(known=known, source=source(ingested_at=at("2026-10-20")))
    proof = coverage(
        covered_from=date(2026, 1, 1),
        historically_known_at=at("2026-01-01"),
        published_at=at("2026-01-01"),
    )
    supplied = provider([value], proofs=[proof])
    assert known.astimezone(UTC).hour == utc_hour
    assert supplied.snapshot("stable-A", known)[1][0].available_at == known
    assert (
        supplied.inspect("stable-A", known.astimezone(UTC)).audit_dict()
        == supplied.inspect("stable-A", known).audit_dict()
    )


@pytest.mark.parametrize(
    "field,bad,code",
    [
        ("version", MAX_REVISIONS + 1, "PIT_VERSION_BOUNDS"),
        ("version", True, "INVALID_VALUE"),
        ("version", "2", "INVALID_VALUE"),
        ("revision_of", 1, "PIT_REVISION_LINK"),
        ("revision_of", True, "INVALID_VALUE"),
        ("cancelled", 1, "INVALID_TYPE"),
        ("covered_until", date(2026, 9, 1), "PIT_COVERAGE_RANGE"),
        ("action_kinds", (), "PIT_COVERAGE_KINDS"),
        ("action_kinds", (ActionKind.SPLIT, ActionKind.SPLIT), "PIT_COVERAGE_KINDS"),
        ("action_kinds", ("split",), "PIT_COVERAGE_KINDS"),
        ("missing", 1, "INVALID_TYPE"),
        ("source", None, "INVALID_TYPE"),
    ],
)
def test_strict_wrapper_types_and_version_resource_bounds(field, bad, code):
    value = document(replace(coverage(), **{field: bad}))
    # Validation precedes hash computation, even when a supplied object is malformed.
    dataset = replace(data(), coverage=(value,))
    assert_error(code, lambda: SyntheticPITProvider(dataset))


@pytest.mark.parametrize("what", ["event", "identity"])
def test_wrong_nested_record_types_rejected_with_stable_code(what):
    value = replace(event() if what == "event" else identity(), record=None)
    dataset = replace(data(), **{"events" if what == "event" else "identities": (value,)})
    assert_error("INVALID_TYPE", lambda: SyntheticPITProvider(dataset))


@pytest.mark.parametrize(
    "field,bad,code",
    [
        ("kind", ActionKind.MERGER, "UNSUPPORTED_ACTION"),
        ("kind", "split", "INVALID_VALUE"),
        ("schema_version", "research_data_v1", "UNSUPPORTED_SCHEMA"),
        ("source", None, "INVALID_TYPE"),
    ],
)
def test_frozen_5A_contract_cannot_bypass_strict_reconstruction(field, bad, code):
    value = copy.deepcopy(event())
    object.__setattr__(value.record, field, bad)
    value = document(value)
    assert_error(code, lambda: SyntheticPITProvider(replace(data(), events=(value,))))


def test_provider_is_immutable_and_unknown_identity_has_no_cached_fallback():
    supplied = provider(identities=[identity()])
    with pytest.raises(FrozenInstanceError):
        supplied.input_sha256 = "tampered"
    assert_error("IDENTITY_MISSING", lambda: supplied.resolve_asof("NO-SUCH", "XNYS", at(), at()))
