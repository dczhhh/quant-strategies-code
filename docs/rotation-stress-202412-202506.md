# Issue #30: fixed stress-window evidence

The [fixed issue contract](https://github.com/dczhhh/quant-strategies-code/issues/30)
selects 2024-12-02 through 2025-06-30 before looking at its results. It does not
authorize tuning to this sample. The baseline is immutable source
`2b5c6029a6cd5fbb9e261cf5726836077b10039e`. G1 freezes current strategy settings,
model USD100000 cash, fees/spread/slippage/participation and whole-share quantum.
It does not silently introduce fractional shares or change the existing v4 report.

## G1: retained sources before new acquisition

`tools/rotation_stress_inventory.py` uses the established exact-source,
owner/main GitHub-hosted Linux context. Its [launcher template](examples/rotation-stress-inventory.yml)
has credential-free preflight, one private inventory writer and fresh read-only
verification. There is no provider key, provider request function, new official
request or Engine invocation. Raw inputs remain in the private data repository
and hosted runners; public logs contain only aggregate counts and flags.

The scope is the reviewed 2024 ETF source family, rotation-2025-v1 groups and
retained issuer documents, not unrelated private archives. Earlier 2024 raw/event
requests are contained in the rotation requests; their pins are recorded without
re-downloading redundant bodies. Later 2025 component dates and execution minutes
cannot supply unique coverage. Group tree paths, reservation parents, original
plan, product identities, body hashes/sizes and original observation clocks are
checked. Only the three relevant raw daily/dividend/split responses per asset are
downloaded, not later minute bodies or adjusted-price views.

The conservative daily range is 2023-11-01 through 2025-06-30: 415 exchange
sessions, including 272 presample and 143 execution sessions. A 252-day return
requires **253 close points**, not 252 prices. At least the closes from 2023-11-29
through the final presample close on 2024-11-29 are needed. The broader range
preserves the issue's requested conservative warm-up.

The existing 13 completed-minute observations are required for all 143 execution
sessions (1859 per asset), not just month starts: daily defensive changes and
delayed settlement funding can submit orders. The NYSE calendar includes half
days, DST and the 2025-01-09 funeral closure. Daily aggregates never substitute
for missing minutes. Three-session requests end at the last session's actual close
minus one millisecond; no unseen 16:00 bar is invented. This exchange calendar is
not historical NSCC settlement certification.

The report enumerates retained daily rows, missing dates, requested-but-absent row
anomalies, missing minute clocks, event-year issuer checks and a finite missing-input
proposal. Requested-but-absent rows are anomalies, not permission to re-request.
Daily gaps use exchange sessions; dividend/split coverage uses all calendar dates
so weekends cannot open unnoticed event-source gaps. No proposal is executed by
this operator. The hard proposal ceiling is 700 requests; the exact smaller count
is computed from retained coverage, not expanded automatically.

The outstanding 2024 issuer-document reservation is validated without reset,
retry or a fabricated HTTP result. A 2025 final tax document never classifies a
2024 distribution. Retained 2025 events are individually checked against same-year
history and existing ordinary-only research rules; failures remain visible. Any
assumed-classification scenario needs its own explicit versioned contract; G1 does
not grant it or strict cash admission.

`inventory.json.gz` and `manifest.json` are append-only under private ref
`research-data/rotation-stress-202412-202506-inventory-v1`. The manifest binds source
commit, LF-normalized operator SHA256, contract SHA256 and report hash. A fresh
runner rebuilds from retained bytes, compares every byte and cannot create a
missing report. Source-integrity failures prevent report creation. Reproducible
inventory with gaps does not make G1 input readiness complete.

## Remaining issue gates

The follow-on [fixed gap-capture launcher](examples/rotation-stress-capture.yml)
uses `tools/rotation_stress_capture.py`. It requires the exact successful G1
bundle hash `025d907bc026fd5c5e3b96271a4161a396bc649a0c6d2e97f14a10eef69e5ad2`
and compares its complete request proposal before reading a provider credential.
Only its 612 requests may run: 51 per asset, three reserved groups of 17. Raw
responses are losslessly gzip/base64 packed into at most four bounded parts per
group. The worst-case content-write budget is 432, including reservations,
manifests, Git trees/commits/refs; each writer retains the existing two-second
pacing and private-visibility check, with at most two capture jobs. Individual
responses retain their own original clocks, status, URL and SHA256. Packing never
changes a price or adjustment basis.

A first non-200 response stops further requests immediately and is retained before
rejection. A timeout retains earlier completed responses and marks the uncertain
request explicitly. Incomplete/unknown groups cannot be reset or retried. A fresh
read-only matrix has no provider credential; neither capture nor source verification
invokes an Engine or attests input completeness. Existing inputs, unknown markers
and prior research reports remain untouched. This follow-on operator executes the
owner-authorized finite proposal; the earlier inventory itself remains read-only
with respect to acquisition.

Actual capture [38864409822](https://github.com/dczhhh/quant-strategies-code/actions/runs/38864409822)
completed its preflight, all 12 capture jobs and all 12 fresh verification jobs.
Each capture made 51 requests (612 total); every verifier made zero requests.
Their plan SHA256 is
`b2100ba76ae1e929f342d28d27c855f2ac8366f659950ee7abc88f9f50350566`.
This is source-retention evidence, not a backtest result.

## G2/G3: conditional replay, not strict-real admission

### Retained v1 failure and reproducibility correction

Source `962bd550e970f404b8eed7d24846a438bfd74b2e` passed public CI (30 jobs)
and ecosystem qualification (12 applicable jobs). Actual operation
[38881861622](https://github.com/dczhhh/quant-strategies-code/actions/runs/38881861622)
passed preflight and the first six-case replay, but its independent verifier failed
`INDEPENDENT_STRESS_REPORT_CONFLICT`. The v1 report bundle
`efa36368b5119d4962870885d270dd371a68a14b727b66d6122725b3266514e1`
is preserved and **not signed off as reproducible**.

A fully invented crash/recovery fixture reproduced a cross-process difference
under `PYTHONHASHSEED=1` versus `33`: equal numeric maxima were serialized as
`0.00` versus `0E-28` at the first monthly close-sensitivity observation and both
tax-variant observations. An unordered asset set determined which equal Decimal
zero retained its representation. Sorting that iteration fixes the representation;
it does not round prices, change a value, adjust a monetary tolerance or tune
strategy parameters. Normal/bear/replacement fresh-process regressions and a full
invented six-case crash/recovery byte comparison cover this defect.

The v2 operator keeps the original input-assumption publication revision at
`962bd550e970f404b8eed7d24846a438bfd74b2e`; a code-only revision does not retag the
assumption document's original observation clock or input fingerprint. Before any
v2 report write, `rotation_stress_report_diagnostic.py` authenticates the complete
pinned v1 bundle, requires **identical normalized input bytes**, and compares all
six logical documents. Only the two declared report schema/operator metadata
fields and exact zero-text spellings at the reproduced computed-sensitivity paths
may differ. Even a USD1E-12 change in cash is rejected; numeric source spelling,
nonzero diagnostic spelling, unknown fields and other computed values are not
waived. No source/financial number is normalized to force a match.

Diagnostics expose file hashes, bounded redacted field paths and difference kinds,
never leaf values, source prices or credentials. A zero-text witness reports only
research dates and zero spellings. A compatible v1 comparison is not acceptance
of its failed fresh run. A separate immutable v2 ref and manifest must still pass
new-runner **every-byte** verification. Replay and verifier deliberately use
different hash seeds (1 and 33); fixing both to one seed would conceal the defect.
No v1 report or unknown reservation is overwritten.

The optional `diagnose` mode is read-only, never creates a report and always labels
its output `NOT_ACCEPTED`; real replay blocks on any undeclared baseline change.
Actual v2 success is pending execution, not asserted by this source change.

The [conditional replay launcher](examples/rotation-stress-replay.yml) checks the
immutable source's CI and ecosystem qualification before private archive access.
It has no vendor credential. `rotation_stress_inputs.py` requires all 415 raw
daily closes and 1859 completed execution bars per asset, bidirectional issuer /
vendor event-date coverage, original ex/record/pay/cash fields and stable CUSIP.
It rehashes all retained source bytes through `ResearchInputs.from_bundle` before
invoking the canonical Engine. Missing minutes are never filled by daily prices.

The real final 2024 classification is still missing. The separately versioned
`config/research/rotation_stress_assumed_2024.json` labels that year's executed
distributions **assumed ordinary**, never issuer-confirmed. Inputs use a distinct
v3 schema and origin, with `assumption:2024` source identity. The Engine rejects
them unless the caller explicitly enables precisely 2024. Default behavior and
strict-real entry remain unchanged. The owner-requested 10% research tax scenario
is not proof of treaty eligibility. Fixed 0% and 30% variants describe sensitivity;
they do not select parameters or establish the account's actual tax treatment.
The additive formal v11 source overlay preserves all previous source pins.
The reviewed compatibility snapshot adds only the optional, default-empty
`ResearchAssumptions.assumed_ordinary_years` parameter; old positional callers
and default ordinary-only behavior remain supported.

`rotation_decision_oracle.py` independently computes original-share reinvestment,
63/126/252 excess momentum, causal SPY regimes and confirmation streaks, rankings,
monthly retention/targets, next-session/expiry timing, conservative execution
targets, risk sell quantities and submitted order intents. Its Decimal breakpoint
allocation and fee arithmetic do not call the production helpers. Read-only
observers check actual Engine call boundaries, monthly completeness and cash
deferrals. The oracle is **conditioned on the separately verified past ledger**;
it is not an independent fill simulator and does not certify every unsubmitted
opportunity. It cannot convert that narrower check into full G2 admission.

`rotation_stress_analytics.py` bridges daily and whole-period asset PnL to NAV at
USD1E-8 without plugs. Reports include monthly returns/drawdowns/holdings/cash/
receivables/fees/turnover, asset contributions and observed risk transitions.
Funded SPY uses the same canonical Engine. A third, lagged-exposure SPY index is
explicitly **ex-post attribution**, not an executable settlement/cost-matched
strategy. Regression and attribution use common close-to-close intervals and
exclude the first partial execution day. CAPM OLS uses deterministic Decimal
arithmetic, fixed Newey-West HAC5 and descriptive 95% normal intervals. The selected
short window has no independent out-of-sample evidence: alpha remains
`INCONCLUSIVE`, irrespective of the estimate's sign.

Daily versus final-RTH-minute prices are never corrected or silently reconciled.
The existing price audit retains exact differences and fixed-holdings mark
sensitivity. A separate conditional signal/rank/target sensitivity uses retained
execution-window minute closes on the same past ledger; it lacks presample
minute coverage and is not a full alternate trading/funding path. Economic cause
and official closing-price equivalence remain unverified.

Each runner executes six Engine cases (rotation and funded SPY at 10%, 0%, 30%).
Inputs, full ledgers, decisions, analytics, original-price diagnostics and provenance
are losslessly retained only in the private data repository. Bounded gzip parts
and a manifest bind every byte, source commit, module hashes, G1 hash and capture
plan. A fresh runner reconstructs all cases and compares every report byte; it
cannot create a missing report. Public output contains safe aggregates only.
The new ref is `research-data/rotation-stress-202412-202506-v2-engine-report`;
the failed v1 remains independently available for audit.

Unified acceptance must distinguish retained-source pass, conditional ledger /
decision / statistical research evidence, and formal blockers. Final 2024
classification, full independent execution-path verification, daily/minute source
causality, NSCC settlement attestation, PIT and brokerage-confirmed credit are
not supplied by this replay. The original account NAV bridge's four unresolved
rows are not repaired by a model backtest. Production strict-real stays blocked
and strategy PR #6 stays open/unmerged; an issue's closed state is not independent
acceptance evidence. The separate fractional-share PR is not silently incorporated.
Owner exclusions and Canadian/ADR new-entry
restrictions are unchanged. Actual backtest numbers may be added only after the
hosted replay and fresh verifier succeed.
