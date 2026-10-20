# Research-only quantity exit admission audit

Issue #42 next-stage prerequisite. This is a separate execution variable, not a frequency
experiment or production change. Source base: `9da73f812b7451706065eb6628da540a3d0de7a5`.

## Confirmed diagnosis

The frozen 2022 0.1-share monthly Engine result was locally reproduced byte for byte, using the
normalized bundle SHA256 `362348c8a126856c065452859ab2ce5fdff95c2823ee7a20f631006ef075ae6e`.
Order ORD-29 on 2022-02-25 14:38 UTC requested a risk exit of
`120.49999999999997` XLRE shares; the research step normalizer stored `120.5` shares.
No XLRE order was pending. Constraint submission allowed the sell. Core cash reservation
rejected it with code `account_restriction`, reason `Position reversal not allowed in cash account`.
The computed residual was `-2.842170943040401e-14` shares, not an economically short position.
All 18 immediate rejected orders in this local monthly replay had that code.

The original Gatekeeper tests the sign of the unrounded sum in `_is_reversal` and
`_is_reducing_order`. The actual FillExecutor already cancels such sums to zero with
`quantity_zero_tolerance`: max(1e-12 shares, 16 ULP at the operands' scale). That inconsistency
blocks an otherwise executable closing order before the executor can apply its existing rule.
Other cases must be diagnosed from their own captured reason/code; do not generalize this one
observation to every rejection.

## Isolated correction and controls

`bind_quantity_exit` is opt-in on a fresh RetrospectiveRotationEngine only. It replaces its
Gatekeeper with a subclass that uses precisely the executor's existing cancellation boundary
for opposite-sign reducing orders. The original Gatekeeper, production source, strategy defaults,
broker state, and order/fill quantities are not edited. Constructor collaborators and settings
are retained, including cash buffer, historical settlement and reservations. Genuine oversells,
short opening orders, buys, cash checks, portfolio caps, liquidity, fees and all other gates remain.
The independent Decimal ledger keeps the original 1e-8 money / 1e-9 fractional representation
thresholds; share-step and no-oversell checks are unchanged.

Synthetic Engine tests generate the residue from actual buy/sell fills, without injecting a
position: 160.1 - 30.3 = 129.79999999999998. The old path rejects the final 129.8-share exit;
the corrected path fills it, closes flat and passes the original independent ledger. An actual
extra 0.1-share sell remains rejected with `short_sale`, no fill and no cash reservation.

## Frozen paired replay protocol

Three independent existing windows: 2022-01-03—2022-12-30; 2024-12-02—2025-06-30;
2025-08-29—2025-11-05. Each keeps its original initial account, tax assumptions, history,
frequency case M/C15_S/C15_SR/T15_S/T15_SR and share step 1/0.1. These are window regressions,
**not** a stitched continuous four-year account. No frequency/anchor/threshold optimization.

For every one of 30 cells, run the original Engine and require complete result AND original
signal-oracle bytes to equal the immutable prior archive, then run only the exit correction.
Capture each submitted request, normalized size, held quantity, pending orders, immediate/final
status and rejection code/reason; retain full controller audit, order journal and fills privately.
Compare identical risk signal paths, the exact pre-divergence trace prefix, all differing
cash/settled cash/receivable/NAV/holding observations and all fills. Reconcile final NAV delta
against per-asset net-PnL deltas at the existing 1e-8 threshold. Report every outcome, including
lower returns after the fix. Existing SPY controls are unchanged frozen references and are not
recomputed by this execution-fix experiment.

Inputs must be rebuilt using the existing retained-source readers and equal prior normalized
bytes. 2022 prior bundle: `fd8a5f947102d6d48e36c05a35c9c17b6db3ad40fd9becc3096a8ed3812e532e`.
Frequency prior bundle: `a807c5025f66a195147bbade33bbda550d3d2ff1d713391b7b42eadb63e39b45`.
There are zero market/broker API requests. Licensed raw inputs, prices and full trade paths stay
in the private repository.

Exact source CI, Ecosystem and research checks must pass before official replay. Each window
gets a new create-only private ref `research-data/rotation-quantity-exit-v1-<window>`.
A fresh independent runner with a different hash seed rebuilds every original and corrected
cell and verifies all complete reports byte for byte without writes. Do not overwrite prior
results or reuse old rankings as corrected results.

## Current acceptance state

Diagnosis and isolated code are implemented; local synthetic/regression checks are in progress.
Official three-window paired replay and fresh-runner verification have not yet completed.
No revised performance ranking is admitted until those gates pass. Continuous 2022—2025 input
coverage audit and the five-group four-year Engine run remain later stages.
`PIT_verified=false`, `performance_admission=false`. Production monthly defaults and research
PR merge state remain unchanged.
