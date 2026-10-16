# Bounded real-input retrospective rotation replay

The owner authorized continuing acquisition and a backtest once inputs pass on
2026-10-15. This operator prepares that explicitly retrospective research run;
it does not change the strict real/PIT factory, strategy settings or PR merge state.

The fixed pool is the existing eleven SPDR sector ETFs and SPY. Execution spans
2025-08-29 through 2025-11-05 (48 sessions), so positions can be opened at the
September rotation before September distributions, followed by October and
November rotations. There are 253 pre-execution raw daily closes from 2024-08-26.
Starting model cash is USD 100,000, with the existing 95% normal target and full
default strategy settings. Spread 1 bp, slippage 2 bps, 1% minute participation,
the disclosed fee-snapshot backcast and 10% dividend withholding are assumptions.

The old 2025 raw-price, original-dividend and split captures are reused read-only,
including the bounded XLE/XLP recovery. Old split-adjusted dividend conflicts stay
unresolved and their derived fields are not consumed. Missing warm-up/tail daily
bars and events plus sixteen three-session minute windows require at most 21 new
vendor requests per ticker (252 total). Minute response bytes are retained; only
the first completed RTH minute, the execution window and closing-minute mark enter
the Engine. Every required observation must exist. Missing bars, unsupported
distributions or ambiguous source identities reject.

The initial retained-only replay reached a daily/minute close disagreement and
stopped before Engine construction. The default `ResearchInputs` policy still
requires matching closes within the unchanged `0.0002` USD source threshold.
This operator explicitly selects the separate research policy
`daily_signal_and_minute_execution_valuation`: raw daily aggregate closes drive
signals, and actual completed RTH minute closes drive fills and portfolio NAV.
The two prices remain separate source-bound records, with distinct source IDs
and URLs required for every closing pair. No minute price is replaced with a
daily close, and no missing observation is synthesized.

[Massive's aggregate documentation](https://massive.com/knowledge-base/article/how-does-massive-create-aggregate-bars)
describes separate timespan construction, trade eligibility and revisions. It
does not prove the cause of each retained discrepancy. The report therefore
counts matched/mismatched closing pairs and reports `reconciled=false` when any
pair exceeds the original threshold. This is an explicit research convention,
not a claim that the source discrepancy has been resolved. SPY's daily-aggregate
gross reinvestment reference also has a different mark convention from strategy
minute NAV, in addition to its different fee/tax treatment.

Separate observations require the version-2 input schema and an explicit policy;
legacy version-1 bundles retain strict matching. Mode, source records and both
prices enter the input fingerprint. New result bundles use the private
`research-data/rotation-2025-v2-engine-report` ref; prior version-1 refs remain
unchanged. Both policies remain isolated historical research and cannot enter
the strict real or synthetic factory.

The separate calendar reference uses weekdays minus September 1 and October 13
within this exact period. The actual Federal Reserve Plaza 2024–2028 holiday PDF
is captured once and bound to its reviewed SHA256
`b8b7ee22fd6f3c275c579b1052e0aa4bfac5394f4ae6c1c502067c6ad7c8284a`.
Its 2025 column was independently read for those dates. This is a disclosed bounded
bank-holiday reference, not an authenticated historical NSCC calendar. The DTCC
a9637 notice was located through official search, but direct PDF URLs returned
404 during preparation; no raw DTCC PDF archive or hash is claimed.

Each ticker reserves its finite group of at most 21 requests before any request. Completed groups
are reused without credentials or source requests. A reservation with no capture
retains unknown outcome and cannot be retried automatically. Each Git write still
checks private repository visibility, and captures, source receipts, normalized
inputs and results are retained only in `quant-strategies-data`. No raw bytes or
prices are printed or uploaded as public Actions artifacts.

Ticker-sized groups reduce private GitHub REST overhead while keeping the same
252 market requests and per-response bound. An invented exact-protocol budget
regression covers capture plus two fresh source verifications and the legacy
input overhead; it must stay under 5,000 archive API calls and 500 content writes.
These two limits follow the [official REST rate-limit documentation](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api).
This does not promise
an unused real token quota or waive any private-write/readback check.
Archive content writes also wait at least two seconds after the previous write
in each writer. With at most two acquisition jobs this leaves room under the
documented 80-content-writes/minute bound. The original private visibility check
runs inside every paced write; read-only verification does not sleep or write.

The operation template is [the fixed workflow](examples/rotation-research-operation.yml).
After source checks pass, replace only its source placeholder with the immutable
reviewed code commit, place it at `.github/workflows/rotation-research-operation.yml`
through an independently reviewed launcher PR to main. The main-only archive
environment is preserved. The strategy PR remains unmerged; only this launcher
workflow enters main. A main push changing that exact launcher path starts the
fixed run. Its owner/repository/event/workflow-path guard, exact source
checkout, hosted-Linux guard and credential-free preflight prevent arbitrary PR
code from receiving archive or provider credentials. Existing main-only collector
guards and environment permissions are untouched. The user's already granted acquisition/backtest authorization
applies; this is not a new request for permission or a strict-real admission waiver.

The replay assembles and hash-checks all retained inputs before Engine construction.
The isolated research broker processes pending orders at the next completed
minute close, explicitly disabling NEXT_BAR's default open-price override and
discarding stale trigger execution prices. All canonical account, window,
causality, settlement, participation and fee checks remain in that path. Tests
use unequal minute opens/closes, variable prices, partial fills and risk exits;
flat OHLC fixtures alone cannot verify this execution-price contract. Sanitized
ledger failures distinguish execution-price, slippage, fee, cash, settled cash,
receivable and NAV mismatches without printing source prices or local values.
Issuer documents retain the original `real_etf_issuer_receipt_v1` observation
time from `fetched_at`; market and calendar captures use `response_received_at`.
These are separate receipt protocols, and neither is replaced by replay time.
After completed acquisition, the [retained-input continuation template](examples/rotation-research-retained-replay.yml)
runs only preflight, replay and independent verification. It receives no provider
secret and contains no acquisition job. Replace its source placeholder with the
checked immutable fix and update the same main-only launcher path. Sanitized
failures report the exception category and repository code position, never the
exception message, local variables, source response or credentials.
Only explicitly numeric ordinary-income compositions matching original cash are
admitted for execution-period distributions; estimated, capital-gain, ROC and
unknown classifications reject. Blank numeric fields are not converted to zero.
Original cash and issuer income fields use the existing issuer protocol's
`1E-8` Decimal matching threshold, including XLSX numeric-literal precision;
the source event amount remains unchanged for cash accruals. Nonordinary fields
must still be explicitly numeric and exactly zero. Status flags are validated
by their own [ICI Primary columns](https://www.ici.org/system/files/2025-09/taxrpt25_primary.pdf):
no indicator in D, blank/R in E and blank/C in F; E/X and misplaced codes reject.
The status, amount-match and nonordinary-component checks report separate reasons.
Warm-up distributions affect gross signal math only. Payment-day credits remain
modeled, with no second withholding. The canonical Engine and independent Decimal
ledger must agree at every input checkpoint. A fresh runner repeats the entire run
without a Massive credential and must reproduce the private report bundle exactly.

The summary reports period return, daily maximum drawdown, modeled fees, fills,
accruals and modeled credits. SPY is a separately labelled gross reinvestment
benchmark without fees or tax; it is not cost-matched strategy performance.
Inputs and results remain retrospective: investor eligibility, historical PIT,
actual broker execution and strict performance admission are not certified.
The owner's original statement NAV discrepancy remains separate.
