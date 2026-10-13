# Research verification contracts (5E-1)

The next separately reviewed implementation is documented in
[5E-2 verification orchestration](research-verification-orchestration.md).
This page retains the accepted 5E-1 schema/binding scope; its reports do not run
the orchestration automatically.

This is the first separately reviewed step of Issue #5's 5E work. It is based on
the accepted 5D commit `2968d1a86db8f021f9ebd73341343329925e046f`, following the
[5E implementation specification](https://github.com/dczhhh/quant-strategies/issues/5#issuecomment-6108446742).
It freezes the declaration schemas, source graph, byte/root binding, authority
decision matrix and redacted provenance. It does **not** implement `verify_dataset`,
filesystem loading, engine factories, CLI admission or market/PIT authentication.

**Issue #5 remains open. Real historical research and real trust issuance remain
blocked.** All `raw_verified`, `PIT_verified`, `archive_ready` and `trusted_entry`
flags remain false. Real Massive archives remain
`deferred_by_user / awaiting_paid_plan_and_license_review`. No network, Secret,
archive creation, trading signal, execution, fee, slippage or settlement behavior
is added or changed by these modules.

## Review sequence

| Step | Deliverable | Admission consequence |
| --- | --- | --- |
| 5D | Accepted versioned synthetic PIT providers | Synthetic evidence only |
| **5E-1, this PR** | Declaration and binding contracts, source graph, threat model and schema reports | No Engine/Broker construction |
| 5E-2, after 5E-1 acceptance | Orchestrate actual 5A/B/C/D interfaces; verify domain terms, clocks, coverage and software versions | Real sources still denied |
| 5E-3, after 5E-2 acceptance | Verified build API, isolated synthetic harness, final-use/recovery binding and pipeline admission | Synthetic harness only; real authority deferred |
| 5E-4, after 5E-3 acceptance | Black-box anti-bypass, failure atomicity and reproducible synthetic E2E | Offline implementation review only |
| 5F and separate real authorization | Licensed independent real evidence and historical E2E | No authorization supplied by this PR |

Each step receives its own stacked PR and independent acceptance before the next
begins. Passing CI does not approve a step or authorize merging its dependency
PRs. The fixed PR #8 integration remains pinned to
`b5ecfd8527fe8eb6398a09b7f74e802e4324789c`; no sibling branch is cherry-picked.

## Versioned declaration schemas

`quant_constraints.research.data.verification_contracts` publishes the
`research_verified_bundle_v1` **declaration envelope** and
`research_verification_contracts_v1` contract version. Its concrete input type is
`FrozenDatasetInput`. The schema name reserves the eventual bundle representation;
a structurally valid envelope is not an issued `VerifiedDatasetBundle`.

| Contract | Bound fields | Current checks |
| --- | --- | --- |
| `ResearchRequest` | Input kind; one stable security ID, contemporary symbol/listing venue, USD; 1m/1d; UTC half-open window and independent asof; NY/RTH session; execution/valuation/risk raw basis and share/volume units; separate raw/signal products and signal basis/units/adjustment version; explicit volume policy; configuration hash; macro/tax/archive policies | Strict types, aware clocks, nonempty/noninverted intervals, distinct products, raw execution/valuation/risk units, coherent signal declarations, explicit known volume policy and feature booleans |
| `EvidenceClocks` | Separate effective, historical knowledge, publication, provider availability and ingestion clocks | UTC normalization; publication cannot follow known time; provider availability cannot precede publication; knowledge/publication/provider cannot follow ingestion |
| `PipelineVersions` | Acquisition, adapter, dataset, normalizer, raw validator, PIT provider/supply, contract and 40-character code revision | Explicit bounded version identifiers; fixed contract version and valid revision syntax |
| `FrozenEvidence` | Source ID/role/provider/dataset/revision/partition/reference, stable ID/event IDs/version, half-open evidence window, all clocks, source dependencies, original immutable bytes and SHA-256 | Exact type, positive version, bounded unique bindings, bytes type/size/hash, separate metadata binding |
| `FrozenDatasetInput` | Request, versions, ordered sources, expected root and schema | Reconstruct each declaration, validate required roles/security owner, enforce source limits, reject graph duplicates/missing dependencies/cycles and root mismatches |

This step checks **declarations and bytes**, not their factual content. An opaque
raw partition can pass this schema without containing valid OHLCV. An event's
declared window does not prove coverage. A supplier/version string does not prove
authority. Source objects with the right class name, labels such as
`adjusted=false` or `verified=True`, and recomputed hashes cannot create real
trust. `ResearchRequest.from_dict` requires every field, rejects unknown fields
and old schemas, and never accepts a verification flag.

Unknown historical clocks stay `None`; ingestion never fills them. An effective
time can lie in the future for an announced schedule. 5E-2 must independently
require and select the actual PIT clocks needed for the query. The code revision
and validator versions here are **declared, hash-bound versions**; they are not
assertions that those programs have run. Later orchestration must check the
actual software and evidence versions before accepting domain results.

The signal declaration binds its basis, share units, adjustment version and volume
policy together. Split-adjusted signals require adjusted share units and an
adjustment version; their volume units depend on the explicit policy:

| Signal basis | Volume policy | Required volume unit | Contract result |
| --- | --- | --- | --- |
| `SPLIT_ADJUSTED` | `AS_TRADED` | `AS_TRADED_SHARES` | Schema declaration accepted |
| `SPLIT_ADJUSTED` | `MULTIPLY_SPLIT_FACTOR` | `SPLIT_ADJUSTED_SHARES` | Schema declaration accepted |
| `RAW` | `AS_TRADED` | `AS_TRADED_SHARES` | Schema declaration accepted; requires original share units and no adjustment version |
| `RAW` | `MULTIPLY_SPLIT_FACTOR` | Either unit | `UNIT_MISMATCH:raw_signal` |
| `TOTAL_RETURN` | Either known policy | Either unit | `UNIT_MISMATCH:signal_basis` |

The two contradictory split-adjusted policy/unit combinations reject with
`VOLUME_UNIT_CONFLICT:signal_volume_unit`. Direct construction and
`ResearchRequest.from_dict` enforce the same rules. Unknown policies still reject
with `VOLUME_POLICY_UNKNOWN`.

RAW remains a declaration that can be stored, not evidence of a successful 5C
comparison. The current 5C raw/comparison algorithm requires the comparison signal
to be `SPLIT_ADJUSTED`; 5E-2 must retain that restriction. TOTAL_RETURN is rejected
at this stage because no current 5C algorithm validates it. It cannot be treated
as split-adjusted. These contracts do not permit adjusted execution/risk values
or expand the validator's supported algorithms.

## Required source graph

Each `source_id` is unique in the envelope. Multiple partitions of one role are
allowed under distinct IDs. `depends_on` names explicit source IDs and is sorted;
the graph is ordered by deterministic topological layers and source ID. Missing
references, cycles and duplicate IDs reject. A declared edge is not proof that
the child was actually derived from the parent; 5E-2 must verify that relationship.

| Roles | Required when | Existing domain interface for 5E-2 |
| --- | --- | --- |
| `raw`, `comparison`, `actions`, `identities`, `facts`, `definition` | Always | The six `ValidationInput` roles and `RawOHLCVValidator`; independent split facts/definition retain factor provenance |
| `pit_events`, `pit_identities`, `pit_coverage` | Always | `PITDataset` / `SyntheticPITProvider` with asof selection, tombstones and continuity |
| `macro_events`, `macro_coverage` | Macro enabled | `PITSupplyDataset` / macro adapter and actual fixed PR #8 gate |
| `tax_sources`, `tax_rules`, `tax_qualifications` | Dividend tax enabled | PIT tax supply and actual fixed PR #8 tax resolver/ledger |
| `archive_manifest`, `l0_response`, `acquisition_receipt`, `l1_partition` | Archive declared present | `ArchivedSnapshot`, `audit_archive` and L0→L1 derivation bindings |
| `license` | Real declaration or archive declared present | Future substantive licensing/authority review; role presence does not grant permission |

Disabled macro/tax policies are explicit and do not require invented empty
evidence. When enabled, every required role must be present. If no archive is
declared, archive roles cannot silently appear. `source_ref` is an opaque bounded
identifier, not a path to open. This module does not read source URLs or files.

The root is SHA-256 of canonical schema, request, versions and all ordered source
metadata. Original bytes contribute their actual digest and byte count. Thus
content, provenance, roles, dependencies, clocks, units, policies, versions and
query/configuration scope belong to the same binding. Input order does not change
the root. No secret is used to sign it, and **a hash proves integrity rather than
source truth, licensing, original price basis or historical availability**.

Sources are capped at 128 per envelope, 2 MiB per source and 16 MiB total, with
bounded identifiers/references and 128 event/dependency bindings per source.
Source versions are positive integers capped at 256.
These are bounded offline schema fixtures, not a design for loading years of
minute data into RAM. Streamed partition handles and actual filesystem admission
belong to later separately reviewed work.

## Authority and issuance boundary

`provenance.contract_decision` reconstructs the snapshot, checks an independently
held root, and then applies this fixed matrix. A caller-chosen "reviewed" root is
still only a declaration/integrity input; no current path treats it as real
authority. All engine permissions are false, including the synthetic path.

| Input kind | Requested mode | Status/code | May continue synthetic validation? | May build Engine? |
| --- | --- | --- | --- | --- |
| synthetic | `synthetic_test` | `synthetic_contract_pass` / `SCHEMA_BOUND_SYNTHETIC` | Yes, after this step is accepted | No |
| synthetic | `verified_research` | `reject` / `SYNTHETIC_NOT_TRUSTED` | No | No |
| real | `synthetic_test` or `verified_research` | `real_evidence_pending` / `REAL_EVIDENCE_DEFERRED` | No | No |
| synthetic or real | `legacy_unverified` | `reject` / `LEGACY_UNVERIFIED` | No certificate issued | No factory in this module |

`synthetic_contract_pass` here means **schema and byte binding only**, not a
successful 5C/5D algorithm run. `trusted_verified` is a reserved enum value and
is unreachable from the decision function. `VerifiedDatasetBundle` is a reserved
opaque type: ordinary construction and restoration raise `TRUSTED_ENTRY_BLOCKED`.
Public reports, dataclasses, enum values, custom `verified=True` flags or manually
constructed objects are never credentials. Even an object made with
`object.__new__` cannot pass the snapshot decision API's exact-type checks.

Existing generic Engine/Broker APIs remain usable and unchanged. The future
mandatory boundary is scoped to **new opt-in verified research and its
publishing/CLI/backtest pipeline**, not arbitrary Python code. Legacy execution
cannot claim verified historical research. Python object privacy, frozen
dataclasses, pickle and module encapsulation are not cryptographic security
boundaries or protection against malicious code with process access.

## Snapshot integrity and restoration

Construction copies and reconstructs request, version, evidence and clock
objects. Input bytes must already be immutable `bytes`; mutable buffers reject.
No paths are re-read during schema checks. Changes to supplied objects after
construction do not change the copied snapshot. Deliberate in-process mutation
of a returned private object is detected on revalidation against the held root.

`snapshot.assert_bound(reviewed_root_sha256)` reconstructs the complete snapshot
before checking the externally held root. Changing content and its leaf hash
fails against the original envelope root. Recalculating every local hash/root
still fails against the original externally held root. Copy/pickle restoration
reconstructs and rechecks the envelope; the restored object's own root is never
proof of authority. A digest stored in an attacker-controlled checkpoint must
not replace the separately frozen source root.

This step has no file loader, streaming reader, engine checkpoint or runtime
admission. It therefore does not claim to detect symlink/path swaps or enforce
TOCTOU protection for a running engine. 5E-3 must bind validation and use to the
same approved immutable content handles, recheck before construction/execution
and on recovery, and reject changed manifests/partitions and root escapes.

## Deterministic redacted provenance

`provenance.data_provenance` emits `data_provenance_v1`, with
`stage="5E-1_schema_and_binding_only"`. The audit records the request window/asof,
stable security/symbol/listing market, currency/frequency/session, separate product
and unit policies, configuration hash, input root, source-role hashes/byte counts,
versions, each separate source clock, explicit feature policies and authority
status/code/phase. Source identifiers/references, provider/dataset/partition
identifiers, event identities and dependencies appear as hashes. Original source
bytes, OHLCV, tax identity strings, URLs/tokens and credentials are absent.

`declared_versions` are the bound claims; `reporter_version` identifies the
contract implementation. `domain_validation_performed`, `coverage_verified`,
`license_verified`, `strategy_config_approved`, `may_build_engine` and the four
authenticity flags are false; `coverage_status="not_evaluated"` and
`source_authority="unverified"`. The audit digest covers the canonical report
without its own digest field. Repeated reports of identical frozen declarations
and mode are identical. Changed asof, configuration, source, units or version
changes the input/report digest or rejects against the pinned root.

An audit dictionary can be edited after emission; it is a report, not a token.
The decision function never accepts that dictionary or a previous success report
instead of the original typed snapshot and externally held binding.

## Threat model and remaining gates

| Attempt | 5E-1 behavior | Mandatory later validation |
| --- | --- | --- |
| Replace any role's bytes and recalculate its leaf hash | Original frozen root rejects | Reparse and run existing validators at 5E-2 |
| Change source/version/event/clock/policy metadata | Original frozen root rejects | Cross-check actual records and sources at 5E-2 |
| Recalculate the entire untrusted root | Original externally held root rejects; real authority stays denied | Approved authority/configuration roots and revalidation |
| Pass `verified=True`, old/missing wire fields or wrong units | Strict declaration decoder rejects | Never accept reports as credentials |
| Missing enabled feature role, owner conflict, graph gap/cycle | Stable `code/field/phase` rejection | Continuous coverage and parent/credit semantics from 5C/5D |
| Correct hashes but adjusted OHLCV disguised as raw | No factual qualification; no engine permission | Actual raw/factor/volume validation at 5E-2 |
| Fake/expired license, independent facts or historical clocks | Real admission denied; no self-reported authority accepted | Independent authenticated evidence, still deferred |
| Cancelled/late PIT version, calendar gap or duplicate credit | No domain validation claimed | Preserve accepted 5D fail-closed behavior and actual #8 interfaces |
| File/symlink/path replacement or source change on engine recovery | No file/engine API exists here | Approved-root handles, final-use/recovery checks at 5E-3 |
| Claim current legacy Engine is globally impossible to bypass | No such claim or legacy interception | Opt-in admission and honest provenance in 5E-3 |

Diagnostics contain stable codes/fields/phases without submitted values or bodies.
The phase names currently used are `contract`, `binding`, `authority`, `issuance`
and `restore`. They do not imply a completed data/engine validation sequence.

The required later execution order remains: explicit request → substantive source
and license review → archive audit if present → 5A normalization/identity → 5C
raw/comparison/independent factors → 5D asof identities/events and enabled macro/tax
evidence → full coverage/hash/version/unit consistency → final-use recheck →
Engine/Broker construction → execution and audit. No later step begins until the
preceding step is independently accepted.

## Validation and acceptance

The regression suite in `tests/constraints/test_verification_contracts.py` tests
each evidence role's replacement with updated leaf hash, source metadata changes,
externally pinned root mismatches, immutable copies/restoration, bogus objects and
verification reports, strict JSON declarations, raw/signal units, role policies,
clocks, owner/graph conflicts, bounds, authority decisions and redaction.

CI adds a separate `check_research_entry_coverage.py` gate requiring each new
module to reach **94% statements / 85% branches**. Original coverage, 5C/5D gates,
fixed PR #8 integration, immutable upstream evidence, security, compatibility,
ecosystem and package checks remain mandatory. Full CI runs for this stacked PR's
`issue-5d-pit-core` base. Neither this implementation nor its green checks approve
5E-1 acceptance, 5E-2 commencement, dependency merges or real historical research.
