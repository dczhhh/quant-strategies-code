# Retrospective rotation execution

Optional tenth-share research uses the separately disclosed
[parent-order commission correction](ibkr-fractional-commission-correction.md),
not commission-free fractional trading. Existing integer-source reports remain
pinned to their original code and fee assumptions.

The separate research factory runs the canonical Engine and constrained cash broker on original-share
inputs. The ordinary `sector_rotation_engine` real entry remains blocked. Research inputs are also
rejected by its synthetic entry. No broker connection or live order is involved.

```python
from decimal import Decimal
from ml4t.backtest.strategies.sector_rotation.research import ResearchAssumptions, retrospective_rotation_engine

scenario = ResearchAssumptions(
    name="CN-resident 10-percent withholding scenario",
    initial_cash=Decimal("100000"),
    dividend_tax_rate=Decimal("0.10"),
    modeled_spread=Decimal("0.0001"),
    slippage_bps=Decimal("2"),
    maximum_participation=Decimal("0.01"),
)
# inputs is a ResearchInputs object bound to retained source observations.
# engine = retrospective_rotation_engine(inputs, scenario)
# result = engine.run()
# report = engine.research_report
```

`ResearchInputs.from_bundle` checks the expected normalized-input hash and every supplied source
body hash before loading. These checks establish byte integrity; they do not authenticate a publisher
or prove historical availability. A collector must independently validate request/receipt/source
identity and derive normalized rows from those sources. Directly constructed fixtures remain labeled
`invented_fixture`. The engine report retains the input fingerprint and each source observation clock.

## Share units and clocks

Gross signals start at one normalized unit and compound original closes, effective split ratios and
original per-share cash. A split changes holding units before an ex-date distribution. The signal
models ex-close gross reinvestment and never posts account cash. Future splits do not rebase earlier
signal values. Only completed RTH closes become visible to the modeled strategy clock.

Source capture clocks remain distinct: the research close source projects a modeled close clock;
corporate-action coverage explicitly retains `point_in_time=False` and the actual later capture time.
The production processor rejects that coverage. Only the bound research processor uses the modeled
effective-date schedule. Context spread and fixed-universe identity are disclosed retrospective
assumptions, not observed historical NBBO or a PIT security attestation. No bid/ask quote is invented.

Warm-up distributions may be explicitly unclassified and used for gross signals only. An execution-
period distribution requires the supported ordinary-fund classification and its source. Unknown,
special, capital-gain and ROC distributions cannot enter the modeled cash lifecycle through this path.
Canadian securities and every ADR remain excluded. The accepted v3 baseline remains **whole-share**
(`share_step=Decimal("1")`). An explicit opt-in `ResearchAssumptions(share_step=Decimal("0.1"))`
enables **one-decimal ETF orders/fills only**. Both the rotation and funded-SPY benchmark
use the same step, with downward rounding for buy/sell order deltas and minute-volume
partial fills; the independent Decimal fee/cash/settlement ledger checks each fill
against the exact tenth-grid. Unsupported steps (0.01, 0.05 and arbitrary precision)
raise at scenario construction. No prior pinned reports are overwritten, and this
research switch does not attest an IBKR security's real-world fractional eligibility.
Some broker fractional trades require instrument/order/notional validation that
historical aggregate bars cannot establish; execution remains an explicit simulation.
The independent fee reference still requires prices of at least one dollar.
Fractional **split cash-in-lieu** remains unsupported and fails closed, distinct from
voluntary tenth-share purchases. Ordinary fractional dividend amounts remain supported.

## Execution assumptions

The research factory fixes next-bar execution at the **next completed minute close**, plus the stated
slippage. This permits completed-minute volume to be used without presenting it as opening liquidity.
The research broker explicitly disables the canonical NEXT_BAR open-price override
when processing these completed events. Pending risk-trigger prices also do not
replace the next observed completed close. Original OHLC inputs remain unchanged,
and order creation must still precede the fill. The ordinary brokers retain their
existing execution lifecycle.
Orders must predate fills. The normal 09:35–09:45 New York window, cash reserve, floating weights,
monthly plan and owner security restrictions are unchanged. The dataset requires every execution-
window minute, the first completed RTH minute and each session's closing mark; gaps are rejected.
Volume participation is capped by the explicit scenario and checked independently.

By default, for each execution session the raw daily signal close and closing-minute mark must
agree within the retained source-rounding limit of 0.0002 USD. A larger difference
requires source reconciliation before Engine construction; the loader does not
choose a preferred price or adjust either series. This does not claim all vendors
define daily and minute closes alike.

An explicit `daily_signal_and_minute_execution_valuation` policy separately uses
daily aggregates for signals and genuine completed-minute bars for executions
and NAV. It requires distinct source records and URLs for each closing pair,
keeps the same completeness gates, and requires an explicit version-2 bundle.
The input fingerprint and report bind the policy and original prices. Its report
counts disagreements at the unchanged threshold and marks them unreconciled;
it does not certify the cause of the differences. Legacy version-1 inputs stay
strict. Both policies remain excluded from the strict real/synthetic entry.

Action identifiers must be unique per security across splits, distributions and
generated modeled credits, so constructing the provider cannot overwrite an event.

Minimum-step cash reservations reject an order that rounds to zero before asking
the fee model for a quote. The constrained rejection audit likewise does not price
a nonexistent fill. Neither path posts cash, positions or fees for that rejection.

Fees use the existing tiered broker model with its disclosed October 10, 2026 normal-charge snapshot
backcast, unknown venue, zero unreported external monthly volume and no rebate. They are simulated
costs, not claimed 2025 bills. The separate Decimal fee reference integrates monthly tier bands and
checks order minimums/caps, venue, clearing, regulatory proxy and pass-through components. It never
calls the production fee calculator.

Tax is an explicit scenario parameter. Ten percent is the owner's requested ordinary-US-dividend
assumption, not a determination of treaty eligibility. Final 2025 Primary information can support a
retrospective composition check; it is not historical knowledge for a 2025 decision. Payable-date
credits are **modeled** cash receipts, not broker confirmation. Source account reconciliation continues
to require actual credit and withholding evidence. Each accrual and modeled credit is posted once by
the canonical corporate-action processor; unheld distributions never create cash.

## Independent ledger and result boundary

After each Engine bar the observer reads cash, settled cash, receivables, quantities and NAV. A separate
Decimal reference starts from the named scenario's cash and zero holdings, applies sourced event
terms and executed fills, checks execution price and costs, and uses an independently supplied
settlement-date list. It does not read production cash, receivable, settlement or fee helpers to
compute its expected state. Positions match exactly; money retains the existing 1E-8 USD tolerance.
An empty replay, missing observations or any mismatch rejects the run. No adjustment is posted.

Observation coverage must equal the complete ordered set of input bar timestamps:
omitted, duplicate, additional and reordered checkpoints are rejected. Each fill
must match its order's security and direction. Committed fee records must identify
the same fill and carry the independently calculated fee; duplicate order or fee
execution identities and out-of-order fills are rejected. The 1E-8 money threshold
is unchanged.

The supplied settlement list must span the run and extend beyond it; its provenance must be explicit.
The 2025 Columbus Day reference case follows [NSCC notice A9637](https://www.dtcc.com/Globals/PDFs/2025/September/03/a9637):
October 10 and October 13 trades settle October 14. Trading sessions and settlement days differ.

`RETROSPECTIVE_ENGINE_LEDGER_PASS` accepts only this conditional historical simulation. The report
sets `engine_invoked=true` when this research Engine is called and reports failures honestly. The
legacy strict-real admission fields (`raw_verified`, `PIT_verified`, `archive_ready`, `trusted_entry`,
`may_build_engine`, `performance_admission`) remain false, and `full_real_scenarios_accepted=0`.
They do not claim this research call is a trusted real entry. Existing 2024/2025 reports and the five
vendor-derived dividend conflicts are untouched. Strategy PR #6 remains unmerged pending review.

The original account workbook NAV bridge stays a separate source-consistency result. A passing
scenario ledger does not explain its residual or certify the original report as reconciled.

## Funded SPY comparator

`retrospective_spy_benchmark(inputs, assumptions)` in `research_benchmark.py` uses
the same original input fingerprint, canonical Engine, completed-minute research
broker, next-minute slippage, participation cap, tiered fee snapshot, tax scenario,
payment-day cash modeling and independent Decimal ledger as the rotation run.
Both start with the same cash and no positions, and finish at the same minute NAV
mark without a terminal liquidation. Cash earns zero interest in both models.

The reference submits one SPY entry at the configured share step at 09:35 in the first execution
session, targeting 95% less the existing 5bp cushion, estimated slippage and fees.
It uses only the completed observation visible at submission. Actual filled
investment depends on integer shares, subsequent prices and available volume;
95% is not a promise of an exact filled weight. The entry may fill only in the
same 09:35–09:45 window for three trading sessions, then expires without retry.
No new orders reinvest distributions or restore the original weight.

A passive single diversified fund permits one position up to 100% and disables
sector-cap drift sales. Those benchmark-only portfolio settings do not change
rotation's 15–25% sector caps, monthly allocation or SPY defense. The reference
is therefore **not exposure matched** to the tactical rotation portfolio.
The return difference is an arithmetic percentage-point comparison, not an
estimate of risk-adjusted alpha. Net receivables remain in NAV even if their
modeled payment falls after the end of the run; withholding is applied once.

The earlier gross SPY daily ex-close reinvestment series remains a separately
labeled diagnostic. The funded reference uses minute marks, actual modeled
share-step-constrained fills, net distributions left as cash and trading costs. Neither
series replaces or reconciles the retained daily/minute close differences.
Private result schema v3 adds the complete funded-SPY trace, fills, actions and
independent ledger report. It preserves the existing rotation fields and uses
`research-data/rotation-2025-v3-engine-report`, leaving v1/v2 results unchanged.
Both successful economic replays are required before the operator can return
success or write the report. Strict real/PIT admission remains blocked.

## Source close diagnostics and valuation sensitivity

Version-4 private reports additionally bind `price-audit.json.gz` and the SHA256
of `tools/rotation_research_price_audit.py` in the immutable source manifest. The
Engine, original inputs and independent ledger are unchanged. The audit never
repairs a price, changes a threshold or marks an unexplained conflict resolved.
It independently decodes the retained minute responses, checks source body hash,
raw/unadjusted identity, millisecond request bounds and ordered minute starts.
Its pair count and mismatches must equal the existing input-policy comparison.

For every security/session pair it privately retains both source IDs, original
closes, signed USD difference, relative basis-point difference and actual minute
starts/ends from five minutes before through five minutes after exchange close.
The last RTH observation starts one minute **before** the exchange close. NY local
times, UTC-derived epochs, DST changes and early closing sessions are explicit.
Per-security and per-session distributions use nearest-rank p50/p90/p95/p99/max;
safe public summaries omit per-date prices, holdings and original source rows.

The original three-session requests run from the first session's open through
one millisecond before the third session's close. Post-close minutes can thus
be inside a retained request for the first two sessions and outside it for the
third. Each candidate is labeled `outside_retained_request`, `requested_no_bar`
or `retained_bar`; none is silently filled. An available post-close minute that
matches a daily close is a diagnostic observation, **not** proof of the listing
exchange's official closing auction, eligible trade conditions or the cause of
the RTH difference. A sparse response is not by itself evidence of lost trades.

[Massive's aggregate construction documentation](https://massive.com/knowledge-base/article/how-does-massive-create-aggregate-bars)
describes trade-eligibility, timestamp and correction differences across time
scales. Its [session documentation](https://massive.com/knowledge-base/article/does-massive-offer-pre-market-and-after-hours-data)
explains inclusive query endpoints and extended-hours bar sparsity. These
present-day documents motivate tests; they do not establish the historical
cause of any archived pair, an official close or historical PIT.

For both executed portfolios, a second calculation keeps each actual end-of-day
quantity, cash and net receivable unchanged and applies the original daily close
instead of the minute mark. It first recomputes the existing minute NAV at the
unchanged 1E-8 tolerance. The alternative return, drawdown and maximum NAV shift
quantify **fixed-holdings valuation sensitivity only**. No order, fill, cash,
tax or receivable is changed; future prices cannot trigger a hypothetical trade.
This is not a re-run of the strategy with alternative signals, an independent
ranking/allocation/order oracle, or a complete decision-sensitivity assessment.
Those remain explicit unverified review items, along with official trade-level
close reconciliation, the original account NAV bridge and strict historical PIT.

The new ref is `research-data/rotation-2025-v4-engine-report`; v1/v2/v3 evidence
is left untouched. Both the economic reports and private diagnostics must match
in the independent fresh run. No new vendor/issuer request is needed.
