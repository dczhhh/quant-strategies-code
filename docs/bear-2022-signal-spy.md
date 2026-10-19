# SPY with the same risk signals: 2022 control

Adds two executable controls (one-share and 0.1-share) to the completed 2022
frequency experiment. Each starts at the current risk tier, then trades only
when the unchanged causal SPY risk regime changes. Normal/warning/bear/deep-bear
targets are 95%/70%/45%/20%, before the existing execution cushion and share rounding.
Downgrades sell only; recovery signals may buy through the existing settlement,
liquidity and rebalance-plan constraints. No calendar top-ups occur without an
actual change in risk tier. Cash earns no interest.

Signals use the previous completed session. Orders execute at the next eligible
completed minute close with the same two-basis-point slippage, one-percent volume
participation limit, historical T+2 cash settlement, approved parent-order fee
model and explicitly assumed ordinary-dividend classification with 10% tax.
The completed-minute adapter also applies to deferred risk orders.

The prior report is fixed by source d45d8e553eff9d2778aa6b8370cb6204c31333d0 and
bundle SHA-256 fd8a5f947102d6d48e36c05a35c9c17b6db3ad40fd9becc3096a8ed3812e532e.
Inputs must rebuild byte-for-byte identically. Both passive SPY controls rerun and
must reproduce the complete prior Engine results exactly. Every observed prior
strategy risk state must agree with the new causal SPY path; otherwise this shared
two-cell comparator is rejected. Ten retained strategy results are reused unchanged.

Full inputs, fills and reports remain private. The report branch is create-only;
a separate fresh runner with a different hash seed rebuilds and verifies all bytes
without writes. No vendor or broker requests occur. No strategy defaults change.

This isolates a useful risk-signal control, but does not match the rotation
strategies' realized exposure. Sector eligibility, position limits, rounding,
partial fills and failed allocations can still change realized exposure. Results
remain conditional research, not point-in-time verification or performance admission.
