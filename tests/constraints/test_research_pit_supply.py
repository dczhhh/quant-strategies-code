"""Synthetic historical macro/tax supply, not actual schedule or tax certification."""

import copy
import json
import pickle
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from test_adapter import at
from test_research_pit import document, source

from quant_constraints.research.data.pit import PITError
from quant_constraints.research.data.pit_supply import (
    MACRO_TYPES,
    MAX_REVISIONS,
    MacroCoverageRevision,
    MacroScheduleRevision,
    PITSupplyDataset,
    QualificationRevision,
    SyntheticPITSupply,
    TaxRuleRevision,
    TaxSourceRevision,
    supply_input_sha256,
)


def common(**changes):
    return {
        "evidence_id": "proof",
        "version": 1,
        "revision_of": None,
        "historically_known_at": at("2026-10-01"),
        "published_at": at("2026-10-01"),
        "provider_available_at": None,
        "source": source(),
        "cancelled": False,
        "document": b"",
        "document_sha256": "",
    } | changes


def macro(**changes):
    return document(
        MacroScheduleRevision(
            **(
                common(evidence_id="cpi")
                | {"event_type": "CPI", "scheduled_at": at("2026-10-12", clock="08:30")}
                | changes
            )
        )
    )


def proof(**changes):
    return document(
        MacroCoverageRevision(
            **(
                common()
                | {
                    "covered_from": at("2026-10-01", clock="00:00"),
                    "covered_until": at("2026-10-20", clock="00:00"),
                    "event_types": MACRO_TYPES,
                    "missing": False,
                    "point_in_time": True,
                }
                | changes
            )
        )
    )


def tax_source(**changes):
    return document(
        TaxSourceRevision(
            **(
                common()
                | {
                    "security_id": "stable-A",
                    "issuer_id": "issuer-A",
                    "event_id": "dividend",
                    "source_country": "US",
                    "effective_from": date(2026, 1, 1),
                    "effective_to": date(2027, 1, 1),
                }
                | changes
            )
        )
    )


def rule(**changes):
    return document(
        TaxRuleRevision(
            **(
                common()
                | {
                    "source_country": "US",
                    "rate": 0.10,
                    "verified_at": at("2026-10-01"),
                    "effective_from": date(2026, 1, 1),
                    "effective_to": date(2027, 1, 1),
                }
                | changes
            )
        )
    )


def qualification(**changes):
    return document(
        QualificationRevision(
            **(
                common()
                | {
                    "account_scenario_id": "scenario",
                    "tax_residence": "CN-mainland",
                    "individual": True,
                    "beneficial_owner": True,
                    "treaty_documents_valid": True,
                    "non_us_ca_tax_resident": True,
                    "effective_from": date(2026, 1, 1),
                    "effective_to": date(2027, 1, 1),
                }
                | changes
            )
        )
    )


def dataset(**changes):
    value = PITSupplyDataset(
        macro_events=(macro(),),
        macro_coverage=(proof(),),
        tax_sources=(tax_source(),),
        tax_rules=(rule(),),
        qualifications=(qualification(),),
        input_kind="synthetic",
        expected_sha256="",
    )
    value = replace(value, **changes)
    return replace(value, expected_sha256=supply_input_sha256(value))


def supply(**changes):
    return SyntheticPITSupply(dataset(**changes))


def tax(provider=None, **changes):
    return (provider or supply()).tax_snapshot(
        **(
            {
                "security_id": "stable-A",
                "issuer_id": "issuer-A",
                "event_id": "dividend",
                "account_scenario_id": "scenario",
                "effective_date": date(2026, 10, 12),
                "asof": at("2026-10-12"),
            }
            | changes
        )
    )


def fails(code, call):
    with pytest.raises(PITError) as error:
        call()
    assert error.value.code == code


def revise(value, when="2026-10-10", **changes):
    if isinstance(value, TaxRuleRevision):
        changes.setdefault("verified_at", at(when))
    return document(
        replace(
            value,
            version=value.version + 1,
            revision_of=value.version,
            historically_known_at=at(when),
            published_at=at(when),
            **changes,
        )
    )


def test_macro_original_revision_cancel_and_historical_requery():
    first = macro()
    second = revise(first, scheduled_at=at("2026-10-13", clock="08:30"))
    third = revise(second, "2026-10-11", cancelled=True)
    p = supply(macro_events=(third, first, second))
    assert p.macro_snapshot(at("2026-10-09")).events == (first,)
    assert p.macro_snapshot(at("2026-10-10")).events == (second,)
    assert p.macro_snapshot(at("2026-10-11")).events == (third,)
    assert p.macro_snapshot(at("2026-10-09")).events == (first,)


def test_macro_dual_fomc_and_all_release_kinds():
    events = tuple(
        macro(
            evidence_id=k,
            event_type=k,
            scheduled_at=at(
                "2026-10-12",
                clock=(
                    "14:00"
                    if k == "FOMC_STATEMENT"
                    else "14:30"
                    if k == "FOMC_PRESS"
                    else "10:00"
                    if k == "ISM"
                    else "08:30"
                ),
            ),
        )
        for k in sorted(MACRO_TYPES)
    )
    p = supply(macro_events=events)
    adapter = p.macro_adapter(event_factory=SimpleNamespace, coverage_factory=SimpleNamespace)
    c, visible = adapter.snapshot(at("2026-10-12"))
    assert c.event_types == MACRO_TYPES and len(visible) == 7
    stamps = {v.event_type: v.scheduled_at.strftime("%H:%M") for v in visible}
    assert stamps["FOMC_STATEMENT"] == "14:00" and stamps["FOMC_PRESS"] == "14:30"
    assert all(v.scheduled_known_at <= at("2026-10-12") for v in visible)


@pytest.mark.parametrize(
    "day,offset",
    [
        ("2026-03-06", 13),
        ("2026-03-09", 12),
        ("2026-11-02", 13),
        ("2026-11-27", 13),
        ("2026-10-11", 12),
    ],
)
def test_dst_halfday_weekend_stamps_are_not_moved(day, offset):
    known = at("2026-01-01")
    end = at("2026-12-31")
    e = macro(
        scheduled_at=at(day, clock="08:30"),
        historically_known_at=known,
        published_at=known,
        source=source(ingested_at=end),
    )
    c = proof(
        covered_from=known,
        covered_until=end,
        historically_known_at=known,
        published_at=known,
        source=source(ingested_at=end),
    )
    p = supply(macro_events=(e,), macro_coverage=(c,))
    visible = p.macro_snapshot(at(day)).events[0]
    assert visible.scheduled_at.astimezone(UTC).hour == offset
    assert visible.scheduled_at.date().isoformat() == day


def test_release_publication_is_separate_from_schedule_and_not_leaked():
    first = macro()
    stamp = at("2026-10-12", clock="08:35")
    second = document(
        replace(
            first,
            version=2,
            revision_of=1,
            historically_known_at=stamp,
            published_at=stamp,
            released_at=stamp,
        )
    )
    adapter = supply(macro_events=(first, second)).macro_adapter(
        event_factory=SimpleNamespace, coverage_factory=SimpleNamespace
    )
    before = at("2026-10-12", clock="08:30")
    assert adapter.snapshot(before)[1][0].published_at is None
    assert supply(macro_events=(first, second)).macro_snapshot(before).events == (first,)
    later = adapter.snapshot(stamp)[1][0]
    assert later.published_at == stamp and later.scheduled_known_at == stamp
    bad = macro(released_at=stamp)
    fails("PIT_RELEASE_KNOWLEDGE", lambda: supply(macro_events=(bad,)))


def test_provider_delay_bounds_both_adapters_and_future_conflict():
    e = macro(provider_available_at=at("2026-10-10"))
    p = supply(macro_events=(e,))
    assert p.macro_snapshot(at("2026-10-09")).events == ()
    assert p.macro_snapshot(at("2026-10-10")).events == (e,)
    second = revise(macro())
    conflict = document(replace(second, scheduled_at=at("2026-10-13")))
    p = supply(macro_events=(macro(), second, conflict))
    assert p.macro_snapshot(at("2026-10-09")).events == (macro(),)
    fails("PIT_REVISION_CONFLICT", lambda: p.macro_snapshot(at("2026-10-10")))


@pytest.mark.parametrize(
    "change",
    [
        {"cancelled": True},
        {"missing": True},
        {"point_in_time": False},
        {"event_types": frozenset({"CPI"})},
        {"covered_until": at("2026-10-10")},
    ],
)
def test_revoked_narrowed_incomplete_coverage_never_revives_old_proof(change):
    first = proof()
    p = supply(macro_coverage=(first, revise(first, **change)))
    assert p.macro_snapshot(at("2026-10-09")).coverage == first
    fails("PIT_COVERAGE_UNKNOWN", lambda: p.macro_snapshot(at("2026-10-12")))


def test_no_event_certificate_and_missing_and_conflicting_coverage():
    assert supply(macro_events=()).macro_snapshot(at("2026-10-12")).events == ()
    fails(
        "PIT_COVERAGE_UNKNOWN", lambda: supply(macro_coverage=()).macro_snapshot(at("2026-10-12"))
    )
    fails(
        "PIT_COVERAGE_CONFLICT",
        lambda: supply(macro_coverage=(proof(), proof(evidence_id="other"))).macro_snapshot(
            at("2026-10-12")
        ),
    )


def monthly_macro_proofs(**november_changes):
    return (
        proof(covered_until=at("2026-11-01", "00:00")),
        proof(
            evidence_id="november",
            covered_from=at("2026-11-01", "00:00"),
            covered_until=at("2026-12-01", "00:00"),
            **november_changes,
        ),
    )


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_adjacent_month_calendars_bind_each_event_at_half_open_boundary(offset):
    october, november = monthly_macro_proofs()
    old_event = macro()
    new_event = macro(evidence_id="november-cpi", scheduled_at=at("2026-11-01", "00:00"))
    now = at("2026-11-01", "00:00") + timedelta(microseconds=offset)
    p = supply(macro_events=(old_event, new_event), macro_coverage=(november, october))
    snapshot = p.macro_snapshot(now)
    assert snapshot.coverage == (october if offset < 0 else november)
    assert snapshot.coverage_proofs == (october, november)
    assert snapshot.event_coverage_proofs == (october, november)
    assert snapshot.events == (old_event, new_event)
    assert set(snapshot.audit_dict()["source_hashes"]) == {
        v.document_sha256 for v in (october, november, old_event, new_event)
    }
    adapter = p.macro_adapter(event_factory=SimpleNamespace, coverage_factory=SimpleNamespace)
    c, events = adapter.snapshot(now)
    assert c.covered_from == october.covered_from and c.covered_until == november.covered_until
    assert c.available_at == at("2026-10-01") and len(events) == 2
    assert c.source not in {"synthetic:" + v.document_sha256 for v in (october, november)}
    fails("PIT_COVERAGE_UNKNOWN", lambda: p.macro_snapshot(november.covered_until))


def test_adjacent_calendar_proof_provider_clock_and_old_asof_are_preserved():
    proofs = monthly_macro_proofs(provider_available_at=at("2026-10-15"))
    p = supply(macro_events=(), macro_coverage=proofs)
    before = at("2026-10-15") - timedelta(microseconds=1)
    earlier = p.macro_snapshot(before)
    assert earlier.coverage_proofs == (proofs[0],)
    adapter = p.macro_adapter(event_factory=SimpleNamespace, coverage_factory=SimpleNamespace)
    c, _ = adapter.snapshot(at("2026-10-15"))
    assert c.available_at == at("2026-10-15") and c.covered_until == proofs[1].covered_until
    assert p.macro_snapshot(before) == earlier


@pytest.mark.parametrize(
    "change",
    [
        {"missing": True},
        {"cancelled": True},
        {"point_in_time": False},
        {"event_types": frozenset({"CPI"})},
    ],
)
def test_bad_adjacent_calendar_is_not_used_to_extend_complete_coverage(change):
    october, november = monthly_macro_proofs(**change)
    p = supply(macro_events=(), macro_coverage=(october, november))
    assert p.macro_snapshot(at("2026-10-12")).coverage_proofs == (october,)
    fails("PIT_COVERAGE_UNKNOWN", lambda: p.macro_snapshot(at("2026-11-02")))


def test_macro_gap_is_not_bridged_and_older_events_are_not_dropped():
    october, november = monthly_macro_proofs()
    october = document(replace(october, covered_until=at("2026-10-31", "00:00")))
    p = supply(macro_coverage=(october, november))
    snapshot = p.macro_snapshot(at("2026-11-02"))
    assert snapshot.events == (macro(),) and snapshot.event_coverage_proofs == (october,)
    assert snapshot.coverage_proofs == (november,)
    fails("PIT_COVERAGE_UNKNOWN", lambda: p.macro_snapshot(at("2026-10-31", "00:00")))
    adapter = p.macro_adapter(event_factory=SimpleNamespace, coverage_factory=SimpleNamespace)
    assert adapter.snapshot(at("2026-11-02"))[0].covered_from == november.covered_from


def test_real_calendar_overlap_rejects_at_boundary_and_through_connected_window():
    october, november = monthly_macro_proofs()
    november = document(replace(november, covered_from=at("2026-10-31", "23:59")))
    p = supply(macro_events=(), macro_coverage=(october, november))
    for now in (at("2026-10-12"), november.covered_from, at("2026-11-02")):
        fails("PIT_COVERAGE_CONFLICT", lambda now=now: p.macro_snapshot(now))


def test_cross_interval_macro_tombstone_and_later_proof_revocation_keep_old_asof():
    original = macro()
    s = source(ingested_at=at("2026-12-31"))
    moved = revise(original, "2026-11-02", scheduled_at=at("2026-11-03", "08:30"), source=s)
    cancelled = revise(moved, "2026-11-03", cancelled=True)
    october, november = monthly_macro_proofs()
    revoked = revise(november, "2026-11-04", cancelled=True, source=s)
    p = supply(
        macro_events=(original, moved, cancelled), macro_coverage=(october, november, revoked)
    )
    old = p.macro_snapshot(at("2026-10-12"))
    assert p.macro_snapshot(at("2026-11-02")).events == (moved,)
    assert p.macro_snapshot(at("2026-11-03")).events == (cancelled,)
    fails("PIT_COVERAGE_UNKNOWN", lambda: p.macro_snapshot(at("2026-11-04")))
    assert p.macro_snapshot(at("2026-10-12")) == old


@pytest.mark.parametrize("e", [macro(missing=True), macro(scheduled_at=at("2026-10-21"))])
def test_event_outside_or_missing_coverage(e):
    fails(
        "PIT_MACRO_EVENT_COVERAGE",
        lambda: supply(macro_events=(e,)).macro_snapshot(at("2026-10-12")),
    )


@pytest.mark.parametrize(
    "revisions,code",
    [
        ((macro(version=2, revision_of=1),), "PIT_REVISION_GAP"),
        ((macro(), document(replace(macro(), version=2, revision_of=1))), "PIT_REVISION_CLOCK"),
        ((macro(), revise(macro(), event_type="PPI")), "PIT_REVISION_KIND"),
        ((macro(historically_known_at=None),), "PIT_KNOWLEDGE_UNKNOWN"),
        ((macro(published_at=None),), "PIT_KNOWLEDGE_UNKNOWN"),
    ],
)
def test_bad_visible_revision_chains(revisions, code):
    fails(code, lambda: supply(macro_events=revisions).macro_snapshot(at("2026-10-12")))


def test_identical_duplicate_and_order_independent_checkpoint():
    value = dataset(macro_events=(macro(), macro()))
    p = SyntheticPITSupply(value)
    expected = p.macro_snapshot(at("2026-10-12"))
    assert len(expected.events) == 1
    for restored in (copy.deepcopy(p), pickle.loads(pickle.dumps(p))):
        assert restored.macro_snapshot(at("2026-10-12")) == expected
        assert tax(restored) == tax(p)
    assert supply_input_sha256(value) == supply_input_sha256(
        replace(value, macro_events=tuple(reversed(value.macro_events)))
    )
    with pytest.raises(AttributeError):
        p.input_sha256 = "bad"
    with pytest.raises(TypeError):
        p._records["macro_events"] = {}


@pytest.mark.parametrize("country,rate_value", [("US", 0.10), ("CA", 0.15)])
def test_tax_source_not_listing_or_execution_venue(country, rate_value):
    p = supply(
        tax_sources=(tax_source(source_country=country),),
        tax_rules=(rule(source_country=country, rate=rate_value),),
    )
    snapshot = tax(p)
    assert snapshot.source.source_country == country and snapshot.rule.rate == rate_value
    audit = snapshot.audit_dict()
    assert audit["status"] == "synthetic_pit_contract_pass"
    assert all(
        not audit[k] for k in ("PIT_verified", "raw_verified", "trusted_entry", "archive_ready")
    )
    assert "issuer-A" not in json.dumps(audit) and "CN-mainland" not in json.dumps(audit)


@pytest.mark.parametrize(
    "field", ["beneficial_owner", "treaty_documents_valid", "individual", "non_us_ca_tax_resident"]
)
def test_account_qualification_explicit_not_inferred(field):
    p = supply(qualifications=(qualification(**{field: False}),))
    fails("PIT_TAX_QUALIFICATION", lambda: tax(p))


@pytest.mark.parametrize(
    "field,value",
    [
        ("issuer_id", "other"),
        ("security_id", "other"),
        ("event_id", "other"),
        ("account_scenario_id", "other"),
        ("effective_date", date(2027, 1, 1)),
    ],
)
def test_unknown_binding_or_expiry_rejects(field, value):
    fails("PIT_TAX_UNKNOWN", lambda: tax(**{field: value}))


@pytest.mark.parametrize(
    "collection,factory",
    [("tax_sources", tax_source), ("tax_rules", rule), ("qualifications", qualification)],
)
def test_tax_tombstones_and_conflicts(collection, factory):
    first = factory()
    p = supply(**{collection: (first, revise(first, cancelled=True))})
    assert tax(p, asof=at("2026-10-09"))
    fails("PIT_TAX_UNKNOWN", lambda: tax(p))
    p = supply(**{collection: (first, factory(evidence_id="second-proof"))})
    fails("PIT_TAX_CONFLICT", lambda: tax(p))


def test_tax_source_future_country_revision_cannot_change_past_rate():
    first = tax_source()
    later = revise(first, source_country="CA")
    p = supply(
        tax_sources=(first, later),
        tax_rules=(rule(), rule(evidence_id="ca", source_country="CA", rate=0.15)),
    )
    assert tax(p, asof=at("2026-10-09")).rule.rate == 0.10
    assert tax(p).rule.rate == 0.15
    assert tax(p, asof=at("2026-10-09")).rule.rate == 0.10


def test_rule_verification_clock_and_qualification_delay():
    p = supply(tax_rules=(rule(verified_at=at("2026-10-11")),))
    fails("PIT_TAX_RULE_UNVERIFIED", lambda: tax(p, asof=at("2026-10-10")))
    assert tax(p).rule.rate == 0.1
    p = supply(qualifications=(qualification(provider_available_at=at("2026-10-13")),))
    fails("PIT_TAX_UNKNOWN", lambda: tax(p))
    fails(
        "PIT_TAX_QUALIFICATION",
        lambda: tax(supply(qualifications=(qualification(tax_residence="CN"),))),
    )


@dataclass(frozen=True)
class ActionFixture:
    security_id: str = "stable-A"
    event_id: str = "dividend"
    kind: str = "CASH_DIVIDEND"
    effective_date: date = date(2026, 10, 12)
    currency: str = "USD"
    terms: str = "ordinary"
    available_at: datetime = at("2026-10-01")
    cancelled: bool = False
    withholding_rate: float = 0.12
    withholding_source: str = "independent:account"
    tax_source_country: str | None = None
    tax_source: str | None = None
    distribution_type: str | None = None
    tax_source_known_at: datetime | None = None


def test_optional_pr8_factories_preserve_account_evidence():
    snapshot = tax()
    original = ActionFixture()
    action, r, q = snapshot.adapt(
        original, rule_factory=SimpleNamespace, qualification_factory=SimpleNamespace
    )
    assert action.tax_source_country == "US" and r.rate == 0.10
    assert q.tax_residence == "CN-mainland"
    assert (
        action.withholding_rate == 0.12 and action.withholding_source == original.withholding_source
    )
    assert original.tax_source_country is None
    assert supply().macro_snapshot(at("2026-10-12")).audit_dict()["record_count"] == 1


@pytest.mark.parametrize(
    "change",
    [
        {"event_id": "other"},
        {"security_id": "other"},
        {"kind": "CASH_CREDIT"},
        {"currency": "CAD"},
        {"terms": "mixed"},
        {"cancelled": True},
        {"effective_date": date(2026, 10, 13)},
        {"available_at": at("2026-10-13")},
    ],
)
def test_action_binding_fail_closed(change):
    fails(
        "PIT_TAX_EVENT_BINDING",
        lambda: tax().adapt(
            replace(ActionFixture(), **change),
            rule_factory=SimpleNamespace,
            qualification_factory=SimpleNamespace,
        ),
    )


def test_pr8_interface_required_instead_of_silent_legacy_tax():
    from quant_constraints.corporate_actions import CorporateAction

    action = CorporateAction(
        "stable-A",
        "dividend",
        "CASH_DIVIDEND",
        date(2026, 10, 12),
        at("2026-10-01"),
        "synthetic",
        dividend_per_share=1,
    )
    fails(
        "PIT_PR8_INTERFACE_REQUIRED",
        lambda: tax().adapt(
            action, rule_factory=SimpleNamespace, qualification_factory=SimpleNamespace
        ),
    )


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("schema_version", "old", "UNSUPPORTED_PIT_SCHEMA"),
        ("version", MAX_REVISIONS + 1, "PIT_VERSION_BOUNDS"),
        ("revision_of", 1, "PIT_REVISION_LINK"),
        ("document", b"tamper", "PIT_DOCUMENT_HASH"),
        ("document", b"", "PIT_DOCUMENT_BOUNDS"),
        ("cancelled", 1, "INVALID_TYPE"),
        ("missing", 1, "INVALID_TYPE"),
        ("event_type", "UNKNOWN", "PIT_MACRO_TYPE"),
        ("historically_known_at", at("2026-10-21"), "PIT_TIME_ORDER"),
        ("published_at", at("2026-10-02"), "PIT_TIME_ORDER"),
        ("provider_available_at", at("2026-09-30"), "PIT_TIME_ORDER"),
        ("released_at", at("2026-10-21"), "PIT_TIME_ORDER"),
    ],
)
def test_record_validation(field, value, code):
    v = replace(macro(), **{field: value})
    if field != "document":
        v = document(v)
    fails(code, lambda: supply(macro_events=(v,)))


@pytest.mark.parametrize(
    "factory,change,code",
    [
        (proof, {"covered_until": at("2026-09-01")}, "PIT_COVERAGE_RANGE"),
        (proof, {"event_types": frozenset()}, "PIT_COVERAGE_KINDS"),
        (proof, {"point_in_time": 1}, "INVALID_TYPE"),
        (tax_source, {"source_country": "GB"}, "PIT_TAX_COUNTRY"),
        (tax_source, {"distribution_type": "mixed"}, "PIT_TAX_CLASSIFICATION"),
        (tax_source, {"currency": "CAD"}, "PIT_TAX_CURRENCY"),
        (tax_source, {"effective_to": date(2025, 1, 1)}, "PIT_EFFECTIVE_RANGE"),
        (rule, {"rate": True}, "PIT_TAX_SCENARIO_RATE"),
        (rule, {"rate": 0.30}, "PIT_TAX_SCENARIO_RATE"),
        (rule, {"rate": float("inf")}, "PIT_TAX_SCENARIO_RATE"),
        (rule, {"verified_at": at("2026-10-21")}, "PIT_TIME_ORDER"),
        (rule, {"verified_at": at("2026-09-30")}, "PIT_TIME_ORDER"),
        (qualification, {"individual": 1}, "INVALID_TYPE"),
    ],
)
def test_additional_term_validation(factory, change, code):
    v = factory(**change) if change.get("rate") != float("inf") else replace(rule(), **change)
    collection = {
        TaxSourceRevision: "tax_sources",
        TaxRuleRevision: "tax_rules",
        QualificationRevision: "qualifications",
        MacroCoverageRevision: "macro_coverage",
    }[type(v)]
    fails(code, lambda: SyntheticPITSupply(replace(dataset(), **{collection: (v,)})))


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("input_kind", "licensed_private", "REAL_DATA_DEFERRED"),
        ("schema_version", "v0", "UNSUPPORTED_PIT_SCHEMA"),
        ("macro_events", [], "PIT_INPUT_BOUNDS_OR_TYPE"),
        ("macro_events", (proof(),), "PIT_INPUT_BOUNDS_OR_TYPE"),
        ("macro_events", (macro(),) * (MAX_REVISIONS + 1), "PIT_INPUT_BOUNDS_OR_TYPE"),
    ],
)
def test_dataset_validation(field, value, code):
    fails(code, lambda: supply(**{field: value}))


def test_hash_terms_invalid_source_type_and_input_type():
    value = dataset()
    fails("PIT_INPUT_HASH", lambda: SyntheticPITSupply(replace(value, expected_sha256="bad")))
    fails("INVALID_TYPE", lambda: SyntheticPITSupply(None))
    e = replace(macro(), scheduled_at=at("2026-10-13"))
    fails("PIT_DOCUMENT_TERMS", lambda: supply(macro_events=(e,)))
    fails("INVALID_TYPE", lambda: supply(macro_events=(replace(macro(), source=None),)))
    fails("NAIVE_TIMESTAMP", lambda: supply().macro_snapshot(datetime(2026, 10, 12)))
    fails("NAIVE_TIMESTAMP", lambda: tax(asof=datetime(2026, 10, 12)))
    fails("INVALID_TYPE", lambda: tax(effective_date="bad"))


@pytest.mark.parametrize(
    "collection,first,second",
    [
        ("tax_sources", tax_source(), revise(tax_source(), issuer_id="different")),
        ("tax_sources", tax_source(), revise(tax_source(), security_id="different")),
        ("tax_rules", rule(), revise(rule(), source_country="CA", rate=0.15)),
        (
            "qualifications",
            qualification(),
            revise(qualification(), account_scenario_id="different"),
        ),
    ],
)
def test_stable_proof_ownership(collection, first, second):
    fails("PIT_EVIDENCE_OWNER", lambda: supply(**{collection: (first, second)}))


def test_unknown_source_schema_and_invalid_query_are_sanitized():
    e = macro()
    bad_source = copy.deepcopy(e.source)
    object.__setattr__(bad_source, "schema_version", "old")
    fails("UNSUPPORTED_SCHEMA", lambda: supply(macro_events=(replace(e, source=bad_source),)))
    fails("INVALID_TYPE", lambda: supply().macro_snapshot("sensitive supplied value"))
    fails("INVALID_VALUE", lambda: tax(event_id=""))
    assert e.scheduled_known_at == e.historically_known_at


@pytest.mark.parametrize("change", [{"distribution_type": "etf"}, {"tax_source_country": "CA"}])
def test_explicit_conflicting_action_classification_or_country_rejects(change):
    fails(
        "PIT_TAX_EVENT_BINDING",
        lambda: tax().adapt(
            replace(ActionFixture(), **change),
            rule_factory=SimpleNamespace,
            qualification_factory=SimpleNamespace,
        ),
    )


def test_boolean_revision_link_cannot_pass_as_integer():
    bad = document(replace(revise(macro()), revision_of=True))
    fails("INVALID_VALUE", lambda: supply(macro_events=(macro(), bad)))
