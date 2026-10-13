# Synthetic PIT Providers (5D)

This contains the three independently reviewable offline 5D work packages from
[Issue #5](https://github.com/dczhhh/quant-strategies/issues/5).
It depends on the [5C offline validator](research-raw-validation.md), the
[5A v2 contracts](research-data-contracts.md) and the existing corporate-action
Provider/Processor. **Only artificial fixtures are accepted.** No real historical
PIT source, raw price, archive permission or trusted research entrance is certified.

## Scope and clocks

`quant_constraints.research.data.pit` adds `PITEventRevision`,
`PITIdentityRevision`, `PITCoverage`, `PITDataset`, `PITSnapshot` and
`SyntheticPITProvider`. `research_pit_synthetic_v1` is a new wrapper schema;
the underlying contracts remain `research_data_v2`. Unsupported wrapper versions
and v1 underlying records are explicitly rejected. This is an explicit migration
path from a validated v2 record plus separately supplied clocks/documents, **not**
an automatic upgrade of a final historical dataset into PIT evidence.

| Clock | Meaning | Query behavior |
| --- | --- | --- |
| `effective_date` / identity effective interval | When an economic fact or identity applies | Retained independently; not evidence it was public |
| `historically_known_at` | Declared earliest public knowledge of this revision | Required to expose the revision |
| `published_at` | Release of the particular source revision | Must not follow its declared historical knowledge |
| `provider_available_at` | Optional documented provider delay | If present, also bounds availability |
| `source.ingested_at` | Present acquisition of the source | Bounds all supplied knowledge clocks; never substituted for them |

The visible time is the maximum of public knowledge, publication and optional
provider availability. Publication or knowledge `None` remains unknown. A 2026
ingestion can hold a synthetic 2025 knowledge declaration, but it does not prove
real 2025 availability. Effective facts and historical knowledge are separate
fields; there is no retrospective-final-version adapter for strategy input.

## Bound input and bounded work

Each wrapper contains exact canonical synthetic document bytes plus SHA-256.
`evidence_payload(revision)` builds these fixtures. The bytes must match all
declared terms, source metadata, clocks and versions; changing bytes, a ratio or
a clock without rebinding is rejected. Recomputing a document or input hash is
not authentication: anyone can author these fixtures.

The caller explicitly sets `PITDataset.input_kind="synthetic"` and freezes
`expected_sha256=pit_input_sha256(dataset)` before constructing the Provider.
The actual input digest includes source metadata, document bytes/digest, events,
identity and coverage revisions and schema. Ordering does not affect the digest.
`licensed_private`, real inputs and missing/wrong bindings are rejected.

All input is strictly checked and privately copied at construction; queries are
read-only. The Provider is frozen and exposes no mutable canonical entries.
Returning an identity produces a copy. There are at most 256 revisions per input
collection, versions at most 256, and 16 KiB per document. Stable-security and
symbol/venue indexes avoid reading unrelated securities; per-security revision
selection is still bounded linear work, not a claim of constant-time history
queries or a fix for PR #4's separate broker full-snapshot performance issue.

Pickle/deepcopy reconstruct and revalidate the canonical checkpoint, rebuilding
indexes. Only use trusted Python pickle inputs. There is no untrusted pickle
loader, network request, Secret, file reader/writer or archive in this module.
Source replacement requires construction and validation of a new Provider.
Private Python state is not a security boundary against deliberate in-process
mutation. Content-bound runtime enforcement belongs to 5E.

## Historical revisions and explicit coverage

For each `(security_id, event_id)` or identity/coverage ID, only revisions visible
at the query instant participate. Versions must be contiguous from 1, knowledge
availability strictly increases, and identical duplicates are deduplicated.
Same-version source/term disagreement is a conflict; event kind changes are
unsupported. Later revisions/known conflicts cannot replace earlier snapshots.
An entirely unknown clock is conservatively unknown, not silently ignored.
Cancelled revisions are tombstones; they are retained for reconciliation.

`inspect(security_id, asof)` has four states:

| State | Meaning within the synthetic contract |
| --- | --- |
| `events` | Valid visible events, including announced future events |
| `proven_no_events` | The selected explicit coverage declares no active visible events; tombstones may still be returned |
| `unknown` | Missing/revoked/non-PIT/partial coverage or unproven knowledge clock |
| `conflict` | Competing revisions/proofs, version gaps or inconsistent credits |

Coverage is inclusive by local New York date, matching PR #4, and must cover all
three supported action kinds. Missing data is not an empty-event certificate.
Revocation/narrowing of a known coverage revision cannot fall back to its older,
broader version; overlapping proofs in the queried continuous component or at an
event date conflict. Adjacent complete intervals are stitched by inclusive local
date, and each visible event is bound to its own date's unique visible proof.
Historical events and tombstones remain available across month boundaries;
unprocessed events are never discarded. The supplied coverage is only the complete
continuous component containing the query date. Its availability is the latest
knowledge clock of its constituent proofs, and its source digest binds all those
proof hashes; the audit also retains individual event-coverage hashes. Missing or
incomplete intervals never extend that component. The existing Processor checks
coverage back to its last observed local date, so gaps between bars reject before
ledger mutation. A dividend and its credit additionally require continuous proof
between their effective dates, even if each endpoint separately has evidence.

`snapshot(security_id, asof)` implements the existing `CorporateActionProvider`
protocol and raises `PITError(code, field)` on unknown/conflict. There is no
`events=[]` fallback for missing evidence. Its `point_in_time=True` coverage flag
describes fixture clock semantics, **not** real `PIT_verified` status.

## Historical identity lookup

`resolve_asof(symbol, venue, effective_at, known_at)` independently selects
historically visible identity versions and then applies the unchanged 5A
half-open effective-interval/rename checks. `IdentityMap.resolve_effective()`
is not changed or relabelled PIT. Before a rename announcement, an old open
mapping may remain the then-known expectation; after announcement, the old
interval is closed and the new symbol retains the same stable security ID.
Actual effective facts from a future revision are not substituted for that
earlier expectation.

Ticker reuse across securities, identity-ID ownership changes, overlaps, missing
predecessors, mergers and delistings remain unsupported/fail closed. Venue is
listing identity, not IBKR SMART execution venue. Rename lookup does not migrate
live positions/orders or permit the old and new symbols simultaneously in the
existing Processor; exposure migration is a separate explicit reconciliation.

## Existing ledger adapter, not a new ledger

Only split/reverse split, ordinary USD cash dividend and independent cash credit
records are converted. An ex-date creates the existing entitlement/receivable;
`payable_date` alone never creates cash. Synthetic credit records require
`CreditEvidence.SIMULATED_ASSUMPTION`; a synthetic document cannot pose as an
actual broker confirmation. A predeclared simulation credit is not exposed to
the ledger before New York midnight on its explicit credit date. Each visible
active credit requires a visible uncancelled dividend parent, a non-earlier
credit date, and no second credit event for that entitlement. Partial/multiple
credits are unsupported.

The unchanged Processor retains single-application/checkpoint semantics. An
economic revision or cancellation **after an applied event** requires its
existing explicit reconciliation; the Provider does not reverse history or
reapply a split. Weekend/missing-bar execution continues to use that Processor's
calendar, without fabricating market bars, fills or fees. Rejection regression
tests verify cash, settled cash, positions, receivables, events, orders and fills
are unchanged; separate diagnostics may record a refusal.

Use this adapter only through explicit synthetic test injection. No Engine
builder or `VerifiedDataset` is added, and old Engine bypass protection is not
claimed. `audit_dict()` returns only state, stable codes/fields, counts, clocks
and hashes; economic terms and document bodies are excluded. A pass is only
`synthetic_pit_contract_pass`:
`raw_verified/PIT_verified/archive_ready/trusted_entry` all remain false.

## Macro schedule supply (5D-2)

`pit_supply` adds an independent `research_pit_supply_synthetic_v1` wrapper;
5A v2 and the 5D-1 wrapper keep their existing formats. Unsupported versions
are rejected, and no final historical file is automatically upgraded to PIT.
`PITSupplyDataset` binds macro, coverage, source-country, rule and investor
qualification collections to actual canonical bytes and an order-independent
SHA-256. Every source revision also binds its own document/terms/clocks. Only
`input_kind="synthetic"` is accepted; re-sealing fixtures does not authenticate
historical sources. All collections have the same bounded revision/document
limits as 5D-1, and copied immutable indexes isolate security/account queries.

`MacroScheduleRevision` keeps `scheduled_at`, schedule knowledge,
source-revision `published_at`, provider availability and ingestion separate.
`released_at` represents the economic release; it must be already known to that
source revision, never a future actual release embedded in an earlier schedule.
An actual delay/announcement needs a later revision. No economic values or
predictions are supplied. CPI, PPI, NFP, PCE (headline/core release), ISM and the
separate FOMC statement/conference types are supported. Fixtures exercise 08:30,
10:00, 14:00 and 14:30 New York times; they are not official historical calendars.

`macro_snapshot(asof)` selects only visible contiguous revisions, detects
same-version disagreements, retains cancellation tombstones and rejects missing,
revoked, overlapping or partial coverage. Supply intervals are `[from, until)`;
an event exactly at a shared endpoint belongs to the later interval. Adjacent
complete calendars are stitched around the query instant, with each uncancelled
event bound to the unique complete proof at its scheduled instant. The snapshot's
`coverage` retains that instant's original proof, `coverage_proofs` lists the
continuous component, and `event_coverage_proofs` retains event-specific evidence.
The adapter uses the component bounds, latest constituent availability and a digest
of all constituent source hashes. It never fills a gap. PR #8's unchanged gate
independently demands its entire session window, including the previous session
when FOMC next-session waiting is enabled. No weekend/holiday event is moved to a
trading day and no absent event
is turned into a no-event certificate. A future conflict cannot replace a past
snapshot. Missing knowledge remains unknown rather than inferred from ingestion.

`macro_adapter(event_factory=MacroEvent, coverage_factory=MacroEventCoverage)`
implements PR #8's existing snapshot protocol through optional injected
constructors. The caller supplies the existing dataclasses when PR #8 is present;
this PR does not import or duplicate that gate. Gate failures keep its existing
buy rejection and risk-sale/RTH behavior. DST, half-day clipping, FOMC union,
rescheduling and cancellation are tested against the pinned sibling interface.

## Ordinary-dividend evidence supply (5D-3)

`TaxSourceRevision` binds stable security, issuer and dividend-event IDs to the
explicit tax-source country, ordinary-stock classification, USD and an effective
`[from, to)` interval. Listing venue, SMART execution venue, currency and ADR
labels do not infer country or qualification. This first synthetic scenario
supports only US 10% and CA 15%; unknown/mixed distributions and other rates or
countries reject. These percentages are approved modeling inputs, not a current
tax determination or proof of actual withholding.

`TaxRuleRevision` separately binds rate, historical availability and verification
time. Verification after the query cannot authorize the historical rule.
`QualificationRevision` binds an explicit account scenario, mainland-China
residency, individual/beneficial-owner status, valid treaty documents and
non-US/CA tax residency. All four eligibility flags must be true and the interval
must cover the dividend date; issuer identity alone proves none of them.

`tax_snapshot(...)` requires security, issuer, event, account scenario, ex-date
and as-of, and rejects missing/conflicting/expired/cancelled evidence. Country
revisions cannot change prior snapshots; selecting the latest revision happens
before interval/qualification evaluation, with no fallback to a revoked proof.
`TaxSupplySnapshot.adapt(action, rule_factory=DividendTaxRule,
qualification_factory=DividendTaxQualification)` returns PR #8 inputs without
changing its resolver, config or ledger. It binds the actual dividend/action date
and preserves independent account-withholding/credit terms. The legacy action
without PR #8 tax fields is explicitly rejected. It never mutates account-wide
qualification or fabricates broker evidence. PR #8 keeps actual withholding
precedence/conflict policy and its capital-gains scenario assumption.

## Sibling integration and remaining stages

The regular suite tests the bounded supply contracts without PR #8 installed.
A separate blocking CI job checks out immutable PR #8 source
`b5ecfd8527fe8eb6398a09b7f74e802e4324789c` into an isolated directory with no
stored credentials. A separate import process verifies that exact SHA and uses
its actual gate, resolver and dividend ledger, injecting this PR's research
namespace only. No feature branch is merged or cherry-picked. Both 5D modules
have additive 94% statement / 85% branch gates, preserving all prior thresholds.

Successful audit output remains only `synthetic_pit_contract_pass`, with all four
real-certification flags false. This completes the three offline work packages
for independent review; authentic historical data remains unimplemented.
5D review precedes 5E's content-bound research entrance; 5F real E2E is blocked.

Real Massive L0/L1/Parquet archives remain
`deferred_by_user / awaiting_paid_plan_and_license_review`. No new Massive calls,
paid subscription or credential changes are needed for this stage. Issue #5 and
the dependent PRs remain open, and no feature PR is automatically merged.
