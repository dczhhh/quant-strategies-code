# Frozen four-year gap acquisition

Issue #42; the owner's continuing authorization covers necessary ETF API use.
This source stage executes only the 2,304 products in the accepted inventory:
private ref `research-data/rotation-four-year-inventory-v1`, commit
`7279fdc6096db6ba273716c31c0790ea58d2b570`, source
`9f31bdde3fb567970b498402fd7bcb4c6e81e0b7`, bundle SHA-256
`8a35353e32f2415a361823cd85971f41ff9b5c8c25fa12d3811835a57a2cc6f0`.
Before any credentials or writes, the complete two-file inventory, parent,
source, policy, logical hash and ordered proposed products must match.

For each of the 11 sector SPDR ETFs and SPY there are 192 products:
188 unadjusted minute products, two unadjusted daily products and two dividend
products. No split request is needed; accepted original split sources already
cover the period. Minute requests span at most three required sessions and never
bridge accepted sessions. Daily/dividend gaps are 2023-01-03 through 2023-10-31
and 2025-11-06 through 2025-12-31. Minute gaps are 2023-01-03 through 2024-11-29,
2025-07-01 through 2025-08-28, and 2025-11-06 through 2025-12-31.

There are six groups of 32 products per asset, with a
create-only reservation and child archive per group under
`rotation-four-year-gap-v1-<ticker>-<group>`. Unknown, partial, rejected and
already reserved outcomes are retained and never reset or retried automatically.
Successful reruns reuse original bytes without a new market request. Raw receipt
timestamps and original bodies are private. Each response is capped at 524,288
bytes, each compressed group at 36 such parts, with bounded expansion and
exact Git blob, bundle, receipt, parent, plan and filename verification.

The launcher example requires complete successful CI, Ecosystem qualification
and specialized capture checks on the frozen source. Only its workflow is
installed on main. A SPY canary precedes the other 11 assets, maximum parallelism
two. A fresh hosted runner then verifies every retained group using hash seed 33,
read-only archive access and no provider key. Source checkout, actor, repository,
main event, exact workflow, Linux hosted runner and disabled debugging are
guarded. Github content writes retain the existing two-second pacing.

This is acquisition and raw integrity validation only. Annual issuer
classification, native cash reconciliation, continuous settlement evidence,
2025-12-05 split integration, continuous Engine results and independent
accounting remain separate admission requirements. PIT and performance
admission flags remain false. Production strategy defaults are unchanged.
