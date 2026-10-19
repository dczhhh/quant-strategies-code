# Fixed rotation and recovery factors

Issue #42 compares one continuous 2022-01-03–2025-12-31 account per case,
initially USD 100,000, without annual resets or spliced short-window returns.
The primary quantity step is 0.1 share; whole shares are a sensitivity.
The first execution anchor is 2022-01-03. There is no frequency, anchor or date search.

| Case | Sector rotation | Risk recovery |
|---|---|---|
| M | Monthly | Monthly |
| M_T15R | Monthly | Every 15 NYSE sessions |
| T15_S | Every 15 NYSE sessions | Monthly |
| T15_SR | Every 15 NYSE sessions | Every 15 NYSE sessions |
| C15_S | Every 15 calendar days, rolling holidays | Monthly |

The first four form a 2x2 experiment. All retain the original daily downgrade
rules, confirmation thresholds, ranking, allocation, price lifecycle, volume
participation, fees and numeric exit correction. The new M_T15R adapter changes
only the risk-update calendar hook; allocation and plan creation stay monthly.
Before the first execution signal, original warmup recovery semantics apply.
The independent oracle enumerates recovery dates separately and recomputes risk
and total returns in Decimal arithmetic.

Two distinct recovery calendars can produce different later risk paths. To
honor the user's same-signal SPY comparison, SPY_SIGNAL_M matches M/T15_S/C15_S,
and SPY_SIGNAL_T15R matches M_T15R/T15_SR. Both trade only actual risk-regime
changes, using 95/70/45/20 percent exposure targets and the original next-bar
execution, fee, liquidity and tax contracts. There are no calendar top-ups
between regime changes. SPY_PASSIVE retains the existing 95-percent initial
buy-and-hold convention, with cash distributions, zero interest and no terminal
liquidation. Thus the matrix has eight accounts per share step, 16 accounts in
total. This matches risk signals; it does not backfit realized sector exposure.

The factorial report must distinguish rotation effects, recovery effects and
their interaction. Every case needs continuous cumulative/CAGR/risk metrics,
annual and quarterly paths, turnover, costs, taxes, holdings/net trades,
orders/fills/rejections, risk transitions, exposure and cash. Annual returns
must use the prior year's actual ending NAV. The 2022 bear, 2023 recovery,
2024 bull and 2025 V-shaped period must remain separately visible.

This module supplies opt-in Engine and oracle wiring, not a market acquisition
or run launcher. Original strategy defaults, old experiment sources and old
reports remain sealed. Invented-source tests run real Engines, verify ledger
and submitted intents, reproduce the complete corrected outputs for existing
cases byte for byte, and compare every SPY signal with the corresponding sector
reference. Real four-year replay still requires annual issuer classifications,
native original-share inputs, historical settlement evidence and the December
2025 split boundary. No new dividend classification assumption is introduced;
PIT and performance admission remain false.
