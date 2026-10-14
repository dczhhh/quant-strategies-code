# SPDR sector rotation: A2 research module

This opt-in module implements the first sector rotation research specification.
It generates buy/sell decisions and is separate from the signal-free
`quant_constraints` account policy. It is research code; synthetic regression
results do not establish strategy profitability or certify real-data PIT quality.

## Policy

The universe is XLB, XLC, XLE, XLF, XLI, XLK, XLP, XLRE, XLU, XLV and XLY.
SPY is the benchmark and market-state input, never a strategy holding.

At each last NYSE session's completed close, rank dividend-inclusive total returns
using 50% of 63-session, 30% of 126-session and 20% of 252-session returns, less the
same SPY returns. The common SPY subtraction does not change sector rankings.
New holdings must be above their 200-session total-return moving average.
Retain existing holdings inside a two-rank buffer, then fill vacant slots.

| SPY state | Target investment | Maximum names | Entry / retention rank |
| --- | --- | --- | --- |
| Normal | 95% | 5 | 5 / 7 |
| Warning | 70% | 4 | 4 / 6 |
| Bear | 45% | 2 | 2 / 4 |
| Deep bear | 20% | 1 | 1 / 3 |

Each nonzero target is 15%–25% of total portfolio NAV. Give each holding 15%,
then water-fill the remaining budget in proportion to its score minus the
weakest selected score. Cap at 25%, redistribute excess, and split equal scores
equally. Negative raw scores cannot reverse the intended ranking.

When only n of N planned slots qualify, invest state_budget × n/N.
Zero qualifying names produces an all-cash target. Prices and partial fills can
temporarily move actual holdings outside the target bounds; these states must
remain visible in order/fill records.

SPY uses completed, causal total-return closes:

- Warning: five consecutive closes below MA200, latest at least 2% below,
  and MA50 at or above MA200.
- Bear: the same break with MA50 below MA200.
- Deep bear: below MA200 and at least 20% below its trailing 252-session high.
  This rule is immediate after the completed close and overrides the confirmation wait.
- Recovery: only at month end, after ten consecutive closes above MA200 and a
  latest close at least 2% above it. MA50 at or above MA200 restores 95%; otherwise
  a previously more defensive state can recover to 70%.

A 20% SPY trigger is not a maximum-loss guarantee. These thresholds are frozen
initial research choices, not optimized or empirically accepted values.

## Inputs and integration

<!-- ml4t-doc-test: sector-rotation-config -->
```python
from ml4t.backtest.strategies.sector_rotation import SectorRotationConfig, strength_weights

policy = SectorRotationConfig()
assert policy.normal_exposure == 0.95
weights = strength_weights({"A": 0.12, "B": 0.07, "C": 0.03, "D": 0, "E": 0}, 0.95)
assert abs(sum(weights.values()) - 0.95) < 1e-12
assert abs(weights["A"] - 0.25) < 1e-12
```

Use these public module entry points:

- `TotalReturnClose(session, available_at, values)`: immutable daily RTH total-return
  indices for all eleven ETFs and SPY. Values must be positive and finite;
  availability must be at or after the exchange's actual close, including half days.
- `CausalCloseSource(closes)`: chronological, immutable input. It reveals only rows
  with available_at no later than the current engine timestamp. Future rows may be
  present in the source but are not exposed to decisions.
- `SectorRotationStrategy(source, policy)`: signals, market state, target allocation
  and auditable decisions. It does not maintain another cash or position ledger.
- `sector_rotation_engine(feed, strategy, context_provider, config, ...)`: the existing
  public Engine with SectorRotationBroker, built on ConstrainedBroker and its
  canonical account, fee model, settlement holds and continuation plans.

A typical caller constructs the source from already validated total-return rows,
the feed from raw intraday OHLCV, and provides execution MarketContext records.
The context must explicitly identify ordinary sector ETFs and provide causal
spread, volume and liquidity availability. A historical total-return label alone
does not prove the upstream series used only contemporaneous company actions.

Massive's split-adjusted close is not a dividend-inclusive total-return series.
The module deliberately rejects `split_adjusted_signal` as its signal data mode.
Do not put paid market data or credentials into this public code repository.

For real-data accounting, provide a PIT `corporate_action_provider` to
`sector_rotation_engine`; its existing processor handles raw-price share
adjustments and cash dividend entitlement/payment. The independently prepared
signal index includes distributions, while the account ledger recognizes actual
cash only when payable. No provider means no automatic dividend cash accounting.

Load research settings with
`SectorRotationConfig.from_yaml("config/spdr_sector_rotation.yaml")`.
A custom ConstraintController must match the policy's 5% cash reserve, target
bounds, first-session execution cadence, total-return signal mode and three-session
continuation length. Other existing account gates remain active.

## Execution and settlement

The monthly signal is generated at the last session's completed close. The
earliest execution is the next session, with NEXT_BAR fills. Decision and execution
prices are never the same completed close.

The adapter limits both automatic plan advancement and fills to the interval
five to fifteen minutes after NYSE open: ordinarily 09:35 inclusive to 09:45
exclusive, America/New_York. This window handles DST and early closing sessions.
A dataset must contain a later observed bar inside the window for an order to fill.

The theoretical portfolio has a 95% normal target. Executable targets reserve
estimated fees, slippage and a configurable five-basis-point uncertainty cushion,
and leave one share quantum inside each bound when integer shares are configured.
Consequently realized investment is near, and can be below, 95%. Targets and the
execution-adjusted targets are reported separately in strategy events. If costs
or whole-share lots make the bounds infeasible, skip that plan and record why;
never borrow, pretend a fill, or reduce the 15% target floor.

Sell legs run before buy legs. Their cash becomes usable according to the existing
historical T+2/T+1 settlement calendar. A continuation buy can execute on the
settlement day in the same morning window; it need not wait until next month.
Plan validity is three execution sessions, capped by the third actual close.

Before every automatic plan advancement and pending fill, the adapter consumes
newly available closes. A SPY downgrade cancels waiting buy plans before settlement
can release funds into a fill. Missing/stale data or lost rank/trend qualification
also cancels a plan. Expiry or cancellation of an incomplete new position below
15% schedules a risk exit rather than leaving it as a formal underweight holding.

Monthly plans can complete across several days. SPY risk reductions can occur
within the month and only sell existing positions, prioritizing the weak names
from the last monthly ranking. Reduce the number of holdings with the investment
tier; never scale five ETFs to 9% each to achieve 45%. No recovery buys occur
midmonth. A bearish state without recovery cannot increase total investment during
a subsequent monthly sector replacement.

The original profile's optional upper-bound drift reductions and other account
risk actions remain in force, subject to legal execution observations. The new
adapter does not alter the original broker, original strategy templates or root API.

## Diagnostics and validation

Inspect `strategy.decisions` for immutable theoretical monthly targets;
`strategy.events` for regime changes, executable targets and skips;
`engine.broker.rebalance_plans`, fills, cash and controller audits for actual execution.

Run `uv run pytest tests/strategies/test_sector_rotation.py --no-cov`.
The tests use synthetic signal indices and execution bars to isolate scheduling,
orders and accounting; the synthetic signal/price relationship is not an investment
performance dataset. Tests cover causal availability, future-row mutation,
floating weights, T+1/T+2 delayed buying, pre-fill SPY cancellation, month-end
recovery, execution windows, missing data, cap-boundary fees and integer shares.

Research performance validation remains a separate step, with the accepted
R1–R5 ablations, SPY benchmarks, costs, settlement exposure and chronological
holdout. No real strategy CAGR, Sharpe or drawdown is claimed by this module.

## Blocking real-data acceptance

The public `sector_rotation_engine` defaults to real-data mode and currently rejects
construction with `UNVERIFIED` before any broker, engine, order or cash mutation.
Only explicitly labelled `validation_mode="synthetic"` regression execution is
available. This is not an admission token for real prices, a real-data validator,
an account statement reconciliation or a certified performance path. Direct use
of generic `Engine` does not constitute accepted sector-rotation execution.

PR #6's real-corporate-actions and real-tax-reconciliation gates remain blocked
pending archived official event/classification evidence, independently verified
raw/split-adjusted/total-return chains and historical PIT evidence. Actual IBKR
gross, withheld tax, fee and net credits cannot be inferred from a payable date.
No real broker statement has been supplied. Historical revised API responses
cannot be relabelled historical knowledge.

The formal constraint package now exposes the reviewed PR #8 versioned country
tax rules and investor qualification. Legacy defaults remain compatible, but
qualified profiles require visible source-country/classification evidence and
explicit eligibility. US 10% and CA 15% are conditional ordinary-stock treaty
scenarios, never deductions inferred from USD or a US listing. ETF distributions,
special dividends, capital returns and unknown classification fail closed for
these profiles. Actual sourced withholding has priority; unexplained conflicts
can be rejected. Non-legacy credits require explicit gross/withheld/fee/net
amounts and reconciliation, not estimated net cash.

GE-style cash-in-lieu remains explicitly unsupported: the processor rejects the
event before changing any position, cash or pending order. It does not create a
virtual tradable 0.375 share or invent a cash-in-lieu price. NVDA/GE official terms
must be independently archived for real-case acceptance; the added regression
fixtures are invented and count as zero real cases.

The opt-in `config/cn_mainland_ordinary_dividend.yaml` leaves qualification null,
uses the current review clock (not a fabricated 2021/2024 PIT clock), and stops
without visible country, distribution and account-scenario evidence. The original
legacy profile file and constructor defaults remain unchanged for compatibility.

The synthetic verifier keeps every original 5A–5E source fingerprint unchanged
as legacy provenance. The formal tax overlay pins four modules separately to
source commit `3cbab8468ac8c4be8d69477588ef105a53246547` and has a distinct synthetic
orchestration version. Old snapshots naming the legacy implementation fail with
`VERSION_MISMATCH` rather than silently approving new runtime source. Refreshing
synthetic input bindings is not new market evidence or historical PIT validation;
existing private capture bytes and budget reservations do not change.
