# Real-data research preparation (5F-0, offline only)

The [5E-4 independent review](https://github.com/dczhhh/quant-strategies/pull/17#pullrequestreview-5485593875)
accepted 5E at `42b4ef2976ade8e70323f8cbf0c21f6740b887de`.
The [5F-0 development authorization](https://github.com/dczhhh/quant-strategies/issues/5#issuecomment-6115843020)
allows permission contracts, synthetic metadata tests, redacted audit and this
preparation plan. The [5F-0 independent review](https://github.com/dczhhh/quant-strategies/pull/18#pullrequestreview-5486001032)
accepted `72a215b5a3a1f8d88a5fdba7c117751274d7047c`;
[5F-1 bounded demand capture](research-demand-capture.md) now covers the separately
authorized development and execution-review boundary. [5F-1 development is accepted](https://github.com/dczhhh/quant-strategies/pull/19#pullrequestreview-5486454320);
the [single-slice review packet](research-slice-execution-review.md) is also
[accepted](https://github.com/dczhhh/quant-strategies/pull/20#pullrequestreview-5486676084).
The [private operator](research-private-capture.md) supplies the runnable local
path; actual account/license/root evidence and independently accepted activation
still precede real capture, followed by real-data and runtime acceptance.
The [Starter update](https://github.com/dczhhh/quant-strategies/issues/5#issuecomment-6115975327)
accepts the user's subscription selection and reported credential upgrade.

The owner has now approved **personal research backtesting** and **bounded,
on-demand downloading to private archives** under the chosen Starter plan, while
explicitly prohibiting purposeless wide-market or multi-year bulk harvesting.
This owner authorization replaces the earlier `deferred_by_user` *intent*
status; it does not itself establish vendor/exchange non-display rights,
long-term storage rights, account entitlements, data authenticity or PIT.

**5F-0 remains a no-I/O preparation gate.** It makes no provider request,
reads no Secret, creates no real archive and issues no real validator or trust
credential. A separately reviewed 5F-1 capture plan must first bind a specific
symbol/window/product, permissions, storage root and hard request/byte budgets.
The verified builder still rejects all real declarations, and
`raw_verified/PIT_verified/archive_ready/trusted_entry/may_build_engine` remain
false. Issue #5 stays open and stacked PRs remain unmerged.

## Inputs and outstanding permission

The user selected **Massive US Stocks Starter** and reported that the existing
credential was upgraded. No account, credential or endpoint has been probed.

| Status | Recorded value | Meaning |
| --- | --- | --- |
| `subscription_selected` | `stocks_starter` | User-approved target plan |
| `credential_upgrade` | `user_reported` | User statement; no Secret read or test |
| `entitlement_verified` | `pending` | Account endpoints/history not independently documented |
| `license_verified` | `pending` | Provider/exchange permission for proposed non-display/backtesting and original/derived archive use not yet independently documented |
| Owner project approval | `bounded_on_demand` | Backtesting and targeted private downloads approved; no broad prefetch, no authority to bypass provider rights |

The public [Stocks plan](https://www.massive.com/stocks), checked on 2026-10-12,
lists five years of history, 15-minute delayed data, minute/second aggregates,
reference/corporate actions, Flat Files and unlimited API calls for Starter.
Trades, Quotes and NBBO are outside this proposed scope. Do not apply Basic's
two-year/five-calls-per-minute defaults to Starter. Public capabilities do not
establish this account's entitlement or authorize unrestricted retention.

The [Market Data Terms](https://massive.com/legal/market-data-terms-of-service)
page identifies its last update as 2025-08-28. Sections 2 and 5 limit non-display
and derivative investment-strategy use without applicable permission; section 8
requires use to stop and data to be deleted upon account termination,
restriction or suspension. Record the applicable agreement and any vendor
exception privately. This gate makes no legal or supplier-license determination.

| Required input | Evidence still needed | State |
| --- | --- | --- |
| Account entitlements | Dated account-specific endpoint/history/frequency evidence, access mode and resource limits | Starter selected; account review pending |
| Use and storage permission | Applicable terms/version and vendor consent covering proposed research, response/derived-data retention and access | Pending |
| User execution scope | Owner approved bounded/on-demand use; exact named security, UTC window, endpoints, requests/bytes and storage root require 5F-1 review | Scope-specific review pending |
| Private storage | Approved root outside Git, access/retention/deletion and recovery policy | Pending |
| Independent action/identity | Issuer/regulator facts, stable security identity and historical symbol intervals | Pending |
| Historical availability | Effective, historically known, published, provider available and ingested clocks with source support | Pending |

5F-0 code and CI must not read or inspect the `massive` Secret, probe API
access, fetch real market evidence or create real L0/L1/Parquet. Only a
separately reviewed 5F-1 execution may use the credential for the approved
specific slice after entitlement/license evidence and a private archive root
are checked. Do not trigger a preflight workflow merely because this plan
passes. The new machine audit state is `preparation_only` with reason
`entitlement_and_license_scope_unverified`, rather than the now-obsolete
`deferred_by_user / awaiting_paid_plan_and_license_review`.

## Machine-readable preparation contracts

The bounded JSON API in `quant_constraints.research.data.permission` uses exact,
versioned records:

| Schema | Required content |
| --- | --- |
| `research_source_permission_v1` | Provider/product/Starter selection, reported upgrade, separate entitlement/license states, license lifecycle, terms version, effective/expiry/review UTC clocks, contact and unresolved questions, account endpoints/history, sample/security scopes, twelve use rights, storage reference, retention days and ACL/deletion review |
| `research_sample_plan_v1` | Sample/symbol/identity, UTC window, calendar/timezone, separate raw/comparison products and units, source/execution venue distinction, clock/correction/factor states, requested access/redistribution/termination uses, storage reference and budgets |
| `research_permission_review_v1` | Exact declaration/sample byte digests, separate account/vendor-written/user-scope/historical-identity/historical-clock references, plus issuer factor reference for split samples; matching sample, permission revision, terms and review times |

The [pending permission declaration](examples/research-source-permission-v1.json)
records Starter selection but grants no right: all twelve rights are `unknown`,
endpoints/history and identity scopes are unset, and real execution is deferred.
`decode_permission_declaration` preserves these states;
`evaluate_permission_preparation` rejects them as `LICENSE_UNKNOWN`.

`evaluate_permission_preparation(permission_bytes, sample_bytes,
review_bytes=..., reviewed_evidence_sha256=..., as_of=...)` takes JSON bytes,
a separately held register digest and explicit UTC review time. The digest must
not be copied from a submitted approval field or self-reported root. The
register binds exact bytes to reviewed references; hashes do not prove consent.
A caller can invent an entire metadata register, so a pass only establishes
**offline completeness and consistency**. Human evidence authentication and new
real-operation authorization remain necessary. Positive fixtures use invented
references, never actual license grants, prices or historical publication times.

Each document is limited to 32 KiB, exact fields/types, sixteen items per list,
unique scopes and UTC clocks. Unknown fields, URLs, raw paths, query strings,
manifest content and credentials do not belong in this format. Document/storage
references are opaque labels; this module never resolves or opens them. Actual
locations, contacts and agreements belong in the private review register.

| Representative rejection | Stable code |
| --- | --- |
| Unknown/pending/expired/revoked/conflicting license | `LICENSE_UNKNOWN`, `LICENSE_PENDING`, `LICENSE_EXPIRED`, `LICENSE_REVOKED`, `LICENSE_CONFLICTING` |
| Account/license documentation pending | `ENTITLEMENT_VERIFIED_PENDING`, `LICENSE_VERIFIED_PENDING` |
| Missing endpoint/history or cross-sample scope | `ENDPOINT_SCOPE_MISSING`, `HISTORY_SCOPE_MISMATCH`, `SAMPLE_SCOPE_MISMATCH` |
| Denied/unknown requested retention, non-display, CI or cloud use | `USE_SCOPE_DENIED_OR_UNKNOWN` |
| Unresolved PIT clock/correction/independent factor | `PIT_CLOCKS_PENDING`, `CORRECTIONS_PENDING`, `INDEPENDENT_FACTOR_PENDING` |
| Missing review root, rehashed replacement or mixed revision | `INDEPENDENT_REVIEW_REQUIRED`, `REVIEW_ROOT_MISMATCH`, `DECLARATION_BINDING_MISMATCH`, `REVIEW_SCOPE_VERSION_MISMATCH` |
| Product units, provider or schema conflict | `PRODUCT_UNITS_CONFLICT`, `INVALID_VALUE`, `UNSUPPORTED_SCHEMA` |
| Extra approval/manifest/Secret field or unsafe path | `FIELDS_MISMATCH`, `UNSAFE_METADATA` |

The deterministic `research_permission_preparation_audit_v1` report contains
only fixed diagnostics. It never echoes submitted values, IDs, paths, contacts,
digests, tokens or manifest bytes. `license_determination=not_performed` always.
Even `OFFLINE_CONTRACT_PASS` leaves `may_acquire` and all five trust flags false.
The existing real builder rejects this report as `INVALID_BUNDLE`; no bundle,
issuer, Engine or acquisition handle is produced. Manually changing report
flags does not create a credential. Arbitrary hostile Python execution is not
made secure by an in-process metadata checker.

## Candidate samples and units

These are **narrow candidate scopes compatible with the owner's on-demand
approval**, not executed requests and not a blanket right to download.
A 5F-1 review must pin one candidate exactly before any API call; changes in
symbol, date, endpoint or budgets require a fresh review. The invented `AAA`
prices in 5E tests remain synthetic observations.

| Candidate | Enclosing UTC request window | Proposed products and purpose |
| --- | --- | --- |
| [AAPL first slice](examples/research-sample-aapl-v1.json) | `[2026-10-09T00:00Z, 2026-10-10T00:00Z)` | Ended RTH session, separate 1m/1d raw and comparison; small receipt/units/identity inventory, no authenticity inference from an uneventful day |
| [NFLX split slice](examples/research-sample-nflx-v1.json) | `[2025-11-14T00:00Z, 2025-11-18T00:00Z)` | RTH 2025-11-14 and 2025-11-17, separate 1m/1d raw/comparison; independent split fact and continuous factor/PIT evidence |
| Cash settlement, later | Separately reviewed interval crossing settlement boundary | Literal causal orders, costs and unsettled proceeds under the existing calendar |
| Dividend/credit, later | Separate ex-date/credit interval and continuous coverage | Eligible shares, receivable, credit once and actual/simulated credit explicitly distinguished |

NYSE calendar calculations place AAPL's proposed RTH at 09:30–16:00 New York
(EDT, UTC 13:30–20:00); NFLX sessions use EST, UTC 14:30–21:00. Both candidates
are Nasdaq listings; NYSE is the selected US-equities session calendar, not a
listing or execution-venue assertion. Stable security IDs and historical symbol
intervals remain **pending**. `us_consolidated` is data scope; SMART/fill venue
is unresolved and cannot be inferred from the source label.

The [Netflix issuer announcement](https://ir.netflix.net/investor-news-and-events/financial-releases/press-release-details/2025/Netflix-Announces-Ten-For-One-Stock-Split/default.aspx)
dated 2025-10-30 describes a ten-for-one split, distribution after 2025-11-14
close and split-adjusted trading expected on 2025-11-17. Public-page review only
selects a candidate. No issuer bytes were archived as authenticated evidence,
and no intraday publication/historical-knowledge clock is established from
this current webpage. No assertion about accessible 2024 NVDA data is made.

Request `adjusted=false` and `adjusted=true` separately for each interval. Raw
execution price/share units and comparison price/share units stay distinct;
volume policy is pending, never automatically multiplied by a split factor.
Licensed original 1m bars would be filtered to RTH; vendor 1d bars retain
`vendor_daily` semantics and are not relabeled RTH. Source revision, precision,
missing-bar and correction policies still need evidence. Never fabricate absent
minutes, prices or volume.

The accepted Engine implementation supports only synthetic single-security USD
RTH 1m/immediate completed-bar data. Starter's 15-minute delay is a capability
constraint, not a fabricated historical availability clock. Actual historical
clocks need evidence. Daily/delayed runtime, symbol transitions, multiple
securities, long streaming and live checkpoint recovery require separate
implementation and acceptance. Do not move availability to bar end to fit the
existing Engine. Proposed daily acquisition does not admit daily execution.

## Evidence chain and remaining gaps

| Layer | Future independently supported evidence | Current gap / insufficient evidence |
| --- | --- | --- |
| 5A declarations | Units, identity, sessions, exact product/revision and normalizer version | An `adjusted=false` parameter, ticker or provider string is only a declaration |
| L0 / 5B | Redacted request, original response pages, account entitlement, receipt time, actual pagination/completeness evidence | No original market bytes; API success/empty page/no `next_url` is not completeness proof |
| L1 / 5B | Original-to-normalized derivation, corrections, conversion policy and software version | No genuine conversion audit; no silent refresh, overwrite or automatic repair |
| L2 / 5B–E | Original-byte hashes, revision, immutable manifest and externally held reviewed root | A self-chosen/recomputed hash does not establish authority or licensing |
| 5C raw/comparison | Actual OHLCV, price/share/volume policy, independent ratio/date fact and factor definition | Vendor raw/comparison and vendor split metadata are correlated sources, not independent counter-evidence |
| 5D PIT | Effective/published/historically-known/provider-available/ingested clocks, revisions/tombstones, historical identity and continuous factor coverage | No genuine historical source; current ingestion or current webpage cannot backfill historical knowledge |
| Macro calendar | Actual historically available schedules, coverage and predecessor sessions | Empty calendars or pinned tests do not install runtime support |
| Dividend/tax qualification | Ex-to-credit coverage, eligibility, source country, applicable rule and verified investor scenario | Payable date is not broker credit; simulated credit and treaty rate are not actual evidence |
| 5E admission/final use | Revalidation from the same original bytes, reviewed independent root and actual config/software/feed binding | Reports, public flags, synthetic pass and handmade bundles cannot issue real trust |

Reuse [archive contracts](research-data-archives.md),
[orchestration](research-verification-orchestration.md) and
[entry/final-use contracts](research-entry.md). Existing raw/PIT validators are
synthetic-only: do not relabel real bytes as synthetic. Licensed cursor-only
pagination remains unverified and rejected; a separately accepted binding and
bounded smoke scope is needed. Do not bypass it using first-page evidence.
Macro/tax runtime remains unsupported. PR #8 stays pinned at
`b5ecfd8527fe8eb6398a09b7f74e802e4324789c` in isolated actual-interface tests;
no merge, cherry-pick or duplicate gate/resolver/ledger is implied.

## Operational questions and human signoff

Obtain written vendor/account evidence for these questions before proposing real I/O:

- Does this exact account/agreement permit personal internal algorithmic
  backtesting and non-display investment-strategy development? Which additional
  vendor/exchange permission is required?
- Which endpoints, dates, aggregate frequencies, original/comparison products
  and Flat Files are actually entitled? What delay, pagination, correction and
  missing-bar/volume policies apply?
- May original raw responses and private L0/L1/Parquet be retained, where, for
  how long, and with which conversion/derived-data restrictions?
- May private cloud services or CI read those records? Identify authorized
  operators, region, processors and access controls. Public CI uses synthetic
  metadata only, even if private-cloud use is later licensed.
- What must be deleted when subscription/account access ends? Record any written
  exception, deletion deadline, backup handling and audit retention. Proposed
  samples do not request post-termination retention or redistribution.

The proposed private root is outside the checkout, referenced publicly by an
opaque label. A reviewer must resolve its actual location privately, verify
owner-only ACL/encryption and grant the operator least privilege. Keep data out
of Git, public logs/artifacts and ordinary backups. Proposed retention is thirty
days, pending vendor/user approval; expiration/revocation stops use and triggers
reviewed deletion including private backups, with a metadata-only deletion
receipt. This document creates no directory, ACL or archive.

| Proposed 5F-1 bound | AAPL | NFLX (separate later slice) |
| --- | --- | --- |
| Logical scope | One ended day, one symbol | Two ended sessions, one symbol |
| Requests, including continuations/retries/evidence | At most 8 | At most 12 |
| Per original source response | At most 512 KiB | At most 512 KiB |
| Total original bytes | At most 4 MiB | At most 8 MiB |
| Enclosing UTC window | 1 day | 4 days |

The owner has approved **targeted acquisition**, not unlimited requests. The
following conservative request/byte ceilings are provisional 5F-1 per-slice
**hard maxima**: they may be reduced during the execution review, never
expanded merely because the plan offers unlimited calls. Nothing is requested
until a slice is pinned, entitlement and rights are checked, and private
storage is approved. Existing hard ceilings remain seven UTC days per
request, 2 MiB per source, 128 sources and 16 MiB per snapshot. Stop before
exceeding any limit; never retry indefinitely, fetch unrelated tickers or
silently truncate a response to appear complete.
401/403, entitlement/license ambiguity or revocation, rate-limit responses,
changed endpoints/terms, unexpected symbols/revisions/units, pagination or clock
gaps, sensitive output, root/ACL mismatch and byte/request overrun all stop the
operation for review. No fallback to a different source, product or credential.

Human signoff must record: reviewer/time, exact account/terms references and
permission revision; sample/security identity and original/comparison units;
UTC/session/delay/PIT scope; immutable evidence register digest held separately;
actual private root/ACL/retention/deletion; total request/byte limits and stop
conditions; accepted 5F-0 HEAD; and evidence that the intended 5F-1 slice
falls strictly within the owner's **already granted bounded/on-demand scope**.
No further blanket owner permission is needed for that approved category, but
5F-1's exact scope and provider entitlement/license evidence must pass the
separate review before executing any request. Pending/ambiguous entries remain
pending. A checked list or API success alone cannot substitute for evidence.

## Separate acceptance steps

| Step | Reviewable deliverable | Consequence |
| --- | --- | --- |
| **5F-0, this work** | Permission/sample/review schemas, offline gate, synthetic tests, redacted audit and operational plan | Accepted at `72a215b`; no real I/O/issuer, five flags false |
| 5F-1, scope-reviewed bounded acquisition | Owner has approved the activity in principle. After 5F-0 acceptance, require account/licensing evidence, exact source/slice/storage budgets and an independently accepted execution review; archive only on demand | Acquired bytes remain untrusted; no Engine |
| 5F-2, separately authorized | Real-source verification extension, independent original facts/identity/PIT and byte/root validation | Reviewed scope only; no general research unlock |
| 5F-3, separately authorized | Controlled real-sample replay and causal accounting evidence against accepted interfaces | Replay approval does not authorize broad issuance or live use |
| 5F-4, separately authorized | Remaining issuer/final-use/recovery scope explicitly fixed after previous acceptance | No automatic merge, Issue closure or expansion beyond accepted scope |

The 5F-0 gate has positive synthetic fixtures and independent rejection cases
for unknown/expired/conflicting/revoked permission, endpoints, retention,
non-display/CI/cloud, PIT clocks, fake approval and self-hash, cross-sample joins,
units/sources, mixed versions, paths/manifests and sensitive metadata. Rejections
never allocate an Engine or change accounts. Future real tests must also retain
factor/ex-to-credit continuity, split/dividend application once, failed-bar
atomicity, causal orders/costs/settlement and pre-run replay from zero. Strategy,
cash, costs, Bracket/OCO and default research policies are unchanged.

The new 5F-0 per-module coverage gate requires 94% statements/85% branches in
addition to all four existing coverage gates. Full CI/Ecosystem, strict rendered
docs, security, reproducible builds, frozen evidence and pinned PR #8 integration
remain required. Preparation acceptance has passed and does not itself
make a real API request, license determination, trusted backtest or 5F-1
execution. The owner's bounded use authorization is recorded; each actual
capture and subsequent real-data backtest must still pass its own evidence
and technical acceptance checks.
