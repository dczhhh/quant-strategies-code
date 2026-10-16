# Fractional commission correction: explicit account-observed research profile

The read-only IBKR connector exposed representative ordinary USD stock executions
split into whole and fractional components under one order ID. The fractional
tails did not each attract the retained 1%-of-notional surcharge. Their charges
were consistent with small clearing/CAT and applicable sell-side regulatory
components. A standalone fractional order, however, carried a broker minimum.
FX conversions and dividend reinvestments were excluded from that inference.
Private account identifiers, quantities, execution prices and rows are not in
this repository or public fixtures.

This is deliberately **not** a claim that fractional shares are free. Nor does
it silently rewrite the public tariff. The current [IBKR commission page](https://www.interactivebrokers.com/en/pricing/commissions-stocks.php)
has a fractional-percent footnote that does not explain these observed ordinary
executions. It also gives DRIP a separate rule. The [internalization guide](https://ibkrguides.com/orgportal/internalization.htm)
distinguishes whole-share and fractional-share components. Source statements
and account observations have different scopes; neither is erased.

## Separate policies

- `published_per_execution` remains the existing fee-model default and preserves
  earlier integer-share snapshots and their interpretation of fractional quotes.
- Explicit `parent_order_tiered` aggregates all executed shares and notional
  under the existing order/day/successful-amendment lifecycle. It applies one
  cumulative marginal broker commission, minimum and notional cap, not an extra
  percent/minimum per fractional tail. Standalone fractions still pay commission.
- Fractional components have zero **external venue proxy** in this internalized
  scenario, not zero total fees. Clearing, CAT, SEC/TAF and applicable broker
  pass-throughs remain. Unknown whole-share venue fees remain disclosed proxies;
  no exchange rebates or universal commission reconciliation are invented.
- The optional 0.1-share rotation and funded-SPY research engines select this
  explicit profile. An independent Decimal ledger and decision reservation
  calculation implement it separately. Money tolerance remains USD `1E-8`.
- DRIP is not modeled by this profile. Dated historical rate gaps and a complete
  account-level fee audit remain unverified. 2024/2025 backtests still disclose
  the existing normal-charge 2026 snapshot backcast, not historical actual bills.

## Comparison and rejection boundaries

`tools/rotation_fractional_replay.py` uses a NEW immutable report ref and compares
integer and 0.1-share scenarios without changing strategy parameters. Before any
report write, its integer control must retain the original normalized input
bytes and economic values. Only the newly defaulted `assumptions.share_step=1`
metadata is removed for that old-report comparison; other values are not rounded
or relaxed. Both share modes must use identical input bytes. A fresh runner must
recompute every byte with a different hash seed. No new provider request is made.

Valid forward splits may retain 0.1-share positions. Off-grid results and reverse
split fractional results requiring cash-in-lieu remain rejected. Canada/ADR buy
exclusions, cash settlement and all strict-real/PIT/broker/performance gates stay
unchanged. An account-observed fee scenario does not certify live execution.
