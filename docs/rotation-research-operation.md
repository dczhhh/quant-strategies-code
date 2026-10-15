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
the Engine. Every required observation must exist. Daily/minute close conflicts,
missing bars, unsupported distributions or ambiguous source identities reject.

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
Only explicitly numeric ordinary-income compositions matching original cash are
admitted for execution-period distributions; estimated, capital-gain, ROC and
unknown classifications reject. Blank numeric fields are not converted to zero.
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
