# Research verification orchestration (5E-2)

The separately reviewed next stage is documented in [5E-3 research entry](research-entry.md).
This page retains the accepted 5E-2 verifier scope; it does not construct an Engine.

This separately reviewed step builds on accepted 5E-1 commit
`fb870fd70a5d3438c20adcdb3c606c04533719a0` and its
[independent acceptance](https://github.com/dczhhh/quant-strategies/pull/14#pullrequestreview-5483397216).
It implements `quant_constraints.research.data.verification.verify_dataset` by
calling the existing 5A/B/C/D implementations on one frozen synthetic input.
It does not implement an Engine/Broker factory, CLI admission, streaming file
loader, final-use/checkpoint admission or an end-to-end ledger replay.

**Only synthetic verification is available.** Real requests stop with
`real_evidence_pending / REAL_EVIDENCE_DEFERRED` before domain decoding or archive
processing. Synthetic input in verified-research mode rejects with
`SYNTHETIC_NOT_TRUSTED`; legacy mode has no certification. Successful orchestration
returns `synthetic_contract_pass / SYNTHETIC_DATASET_CONSISTENT`, never
`trusted_verified` or `VerifiedDatasetBundle`. All `raw_verified`, `PIT_verified`,
`archive_ready`, `trusted_entry` and `may_build_engine` values remain false.

Issue #5 stays open. Real Massive archives remain
`deferred_by_user / awaiting_paid_plan_and_license_review`. This work adds no live
Massive calls, Secret access, real archive acquisition, trading signals, execution
or account changes. Temporary synthetic fixture copies are removed after audit.

## API and input binding

```python
from quant_constraints.research.data.provenance import ResearchMode
from quant_constraints.research.data.verification import verify_dataset

report = verify_dataset(
    frozen_input,
    ResearchMode.SYNTHETIC_TEST,
    reviewed_root_sha256=independently_held_root,
    # Both identifiers are required only if dividend_tax_enabled is true:
    tax_issuer_id=explicit_issuer_id,
    account_scenario_id=explicit_account_scenario_id,
)
```

The caller supplies the original `FrozenDatasetInput` and an independently held
root from the [5E-1 contracts](research-verification-contracts.md). Passing a
successful report, domain dataclass, provider object, constructor-shaped object
or self-reported `verified=True` does not substitute for those bytes. Snapshots
are reconstructed, the held root is compared, and the same snapshot is checked
again before success is reported. This is integrity binding, not authentication
of a caller-chosen root and not protection against arbitrary Python execution.

Issuer and account scenario query identifiers are bounded explicit inputs. Their
combined digest is included in `data_provenance_v1`; identifiers and qualification
documents are not published. The request's configuration digest stays bound, but
`strategy_config_approved=false`: this step has not inspected or approved a
strategy configuration. 5E-3 must bind its actual configuration and query to the
same input rather than treating this report as permission.

## Strict transport and source relationships

`verification_wire.encode_records` transports existing contract records as
`research_verification_payload_v1` JSON. Each record role carries exactly
`schema_version`, `role` and `records`. Each record includes all of its current
dataclass fields; schema defaults are not silently supplied. Dates and timestamps
are ISO strings, enums are strings, immutable collections are arrays and each
document is `{"hex": "..."}` containing its exact original bytes. This fixture
encoder is not a source signer or validator.

Verification decodes only the fixed role-to-contract table. It rejects unknown,
missing and old fields, duplicate JSON keys, non-finite JSON literals, malformed
dates/enums/hex, bool-as-number substitutions, excessive depth and size. Existing
5A constructors and normalizers still own their strict schema rules. No dynamic
class import, pickle decoding or user callback is accepted as input.

The 5E-1 limits remain: 128 sources, 2 MiB per source and 16 MiB total. A role can
have multiple source chunks; each chunk must agree with its records' provider,
dataset, revision, partition and reference. Different partitions are separate
chunks. Each chunk's declared window equals the request's half-open window;
actual record/coverage intervals remain in its original payload and are checked
independently. Stable security ownership, event/identity/coverage/evidence IDs and
revision numbers are cross-checked rather than trusted as labels.

Metadata clocks summarize the chunk without replacing individual clocks: one
shared effective timestamp is retained, otherwise it is unknown; a knowledge,
publication or provider clock is the maximum only when every record declares it,
otherwise it remains unknown. Ingestion is the maximum actual source ingestion
where the underlying contract supplies it. Split facts separately retain their
exact publication clock; they do not invent historical knowledge or an ingestion
clock that the 5C fact contract does not contain. Original record clocks survive
wire reconstruction unchanged.

| Child role | Required bound dependencies |
| --- | --- |
| raw | All identity chunks |
| comparison | All raw, identity, action, fact and definition chunks |
| definition | All action and independent fact chunks |
| PIT events | All PIT identity and coverage chunks |
| macro events | All macro coverage chunks |
| tax sources | All tax rule and qualification chunks |

Disabled macro or tax features reject corresponding unused evidence rather than
silently accepting it. Enabled roles must be present. Empty arrays are not
coverage proof: actual 5C/5D operations decide whether the required query can be
answered. There is no invented placeholder event or price.

## Actual execution order and domain scope

| Phase | Existing implementation | Additional joins at this boundary |
| --- | --- | --- |
| Binding and authority | 5E-1 reconstruction, required roles and held-root comparison | Reject real/verified-research/legacy modes before domain work |
| Versions | Actual 5A/B/C/D version constants and reviewed source files | Reject declared version/code-base mismatches and changed implementation bytes |
| Transport | Strict payload decoder and 5A contract constructors | Validate source metadata/dependency joins; schema construction does not certify the domain |
| Archive, if present | 5B `audit_archive`, `normalize_archived_snapshot`, `iter_raw_bars` | Audit original pages/receipts; reproduce L0→L1; compare exact Parquet/report bytes and canonical raw input |
| Normalize | 5A `normalize_identities`, `normalize_raw_bars`, `normalize_signal_bars` | Compare request/products/units/window/availability and effective identity |
| Raw | 5C `RawOHLCVValidator.validate` | Reject reported missing RTH minutes and missing daily trading sessions |
| PIT | 5D `SyntheticPITProvider.inspect`, `resolve_asof`, existing revision/coverage helpers | Cover the full factor interval/anchor; join visible split versions and historical identities to 5C inputs |
| Supply, if enabled | 5D `SyntheticPITSupply.macro_snapshot`, `tax_snapshot`, existing coverage helpers | Require entire requested exchange sessions; extend related FOMC sessions to continuous predecessor coverage; query explicit issuer/account evidence for visible dividends |
| Completion | Recheck the frozen snapshot/root | Publish all successful stage diagnostics atomically, with no account mutation |

No raw-validation, macro gate, tax-rate resolver or ledger algorithm is copied.
The optional PR #8 integration remains separately tested against the actual fixed
SHA `b5ecfd8527fe8eb6398a09b7f74e802e4324789c`. This step validates supply evidence;
it does not apply a macro trading policy or calculate/credit withholding. Those
existing gate/resolver/ledger interfaces retain their behavior.

The existing 5C support restrictions remain. Comparison must be SPLIT_ADJUSTED,
with the known policy/unit pair from 5E-1, independent fact bytes and supported
split version semantics. RAW is a storable declaration and rejects here with
`UNIT_MISMATCH:signal_basis:raw`; TOTAL_RETURN already rejects in 5E-1. A window
without the split evidence required by 5C is not silently accepted as an
unadjusted comparison. No prices, volumes, factors, identities or events are
repaired, filled or re-sealed as a remedy for a failure.

PIT queries preserve the explicit asof. Future knowledge is not inferred from
ingestion, cancellation is selected before filtering, and old versions are never
revived. The existing 5D parent-to-credit continuity and duplicate-credit checks
still run. Factor evidence must agree with the selected PIT split terms; an
unsupported corrected/cancelled split rejects even when a separately consistent
5C comparison exists. Every raw bar's effective identity is also checked against
the knowledge-visible historical identity, including interval endpoints.

Macro proof at one instant is insufficient: the existing contiguous-coverage
helper must cover complete requested exchange sessions, including half days/DST.
A visible, noncancelled FOMC is relevant only when those sessions include its
`SessionCalendar.first_affected_session` or the next session after its actual
exchange day. A release after a half-day close may already first affect that next
session; it must not add a second following session. As in fixed PR #8, a schedule
on a nontrading day does not invent a next-day blackout.

For each related studied session, require a connected proof from its previous
session's market open through its own market close, including the closed interval
between them. Adjacent coverage segments pass; a microsecond gap still rejects.
Unrelated old or known future FOMC schedules do not expand this scope, but the
original 5D snapshot still validates their own scheduled-time evidence. This is
full-session supply integrity, not approval of configuration-dependent blackout
windows or next-session trading policy. Tax evidence is queried
for every visible, noncancelled ordinary dividend, retaining the existing source
country, US/CA scenario rate, rule-verification and qualification restrictions.
If there are no visible dividends under complete corporate-action coverage, tax
application is explicitly `not_applicable_no_visible_dividends`; that is not a
qualification approval or a fabricated tax decision.

## Optional frozen synthetic archives

`verification_archive.encode_archive` transports an existing synthetic 5B fixture
as `research_verification_archive_payload_v1`: the canonical manifest and an exact
path/hex file inventory. Verification accepts one source-layer pack and one
normalized-layer pack, binds both to the request and their manifest revisions,
and requires a separate license-role leaf matching the source's synthetic access
declaration. The source pack depends on that leaf; the normalized pack depends on
the source pack. Separate L0-response, acquisition-receipt and L1-partition leaves
must match their manifested bytes/references/paths/revisions and depend on the
corresponding pack. Extra or missing leaves reject.

Verification never opens an incoming archive path. It materializes these bounded
frozen bytes in a private temporary directory using existing 5B safe paths,
audits both snapshots, reruns the actual normalizer and compares the reproduced
manifest and all L1 bytes. Even a hash-consistent but differently derived L1
rejects. Parquet replay must equal the same canonical 5C raw records. Temporary
copies are removed on success and failure; no verified archive is published.
Only this newly created internal temporary root is canonicalized, so OS aliases
such as macOS `/var` work without weakening 5B's archive-symlink rejection.

This temporary fixture adapter does not implement the future approved-root
filesystem/streaming loader, symlink admission or checkpoint/final-use pipeline.
It also does not validate real license terms. A synthetic access declaration is
checked for internal consistency only; `license_verified=false` and
`archive_ready=false` remain explicit.

## Versions, reports and acceptance

The profile checks actual acquisition, adapter, dataset, normalizer, raw validator,
PIT provider/supply and contract constants against declarations. It requires
`code_revision=fb870fd...`, the accepted implementation base, and compares the
installed Python source files against source fingerprints from that immutable
base. Git's CRLF/LF checkout conversion is normalized for Python source only;
evidence bytes are never normalized. The report includes the checked profile and
actual fingerprints of the three new orchestration modules. This checks installed
source files, not process attestation or protection from malicious monkeypatching.

The deterministic report retains the 5E-1 redacted provenance, input root,
configuration/query digests, actual version checks and the safe existing domain
diagnostics. Failed runs publish no partial successful stage reports or software
approval. Stable code/field/phase are retained; unknown submitted field names,
documents, economic values, private issuer/scenario IDs and exception details are
not included. Modifying a report dictionary cannot authorize any entrance.

All five 5E modules have an additional **94% statements / 85% branches per-module**
coverage gate, alongside unchanged original, 5C and 5D gates. Full CI triggers on
this stacked base; the fixed PR #8 actual interfaces and complete Ecosystem matrix
remain mandatory. Success permits independent **5E-2 synthetic implementation**
review only. 5E-3 begins after that acceptance, and 5E-4 receives its own acceptance.
No stage or CI run merges the dependency PRs or releases real historical research.
