# Synthetic research entry: black-box acceptance (5E-4)

The [5E-4 independent review](https://github.com/dczhhh/quant-strategies/pull/17#pullrequestreview-5485593875)
has accepted this synthetic/offline stage. The
[5F-0 offline permission preparation](research-real-readiness.md) records the
Starter selection, pending license/entitlement inputs and future acceptance
sequence; real work remains deferred.

This stage tests the [reviewed entry](research-entry.md) with actual orders,
fills, positions, reservations, receivables and execution costs. It extends the
5E-3 wiring evidence; passing these tests does not issue real historical trust.
Implementation and CI evidence require independent 5E-4 review before the
offline 5E milestone can be accepted.

## Actual execution

`tests/constraints/test_research_entry_e2e.py` creates invented complete RTH
minute intervals, an independent split fact and the original frozen PIT graph.
It calls `build_synthetic_test_engine` with literal, configuration-bound test
orders. Positive runs use the actual verifier, provider, DataFeed, Engine,
ConstrainedBroker, fee/slippage models and corporate-action processor without
replacing them with success stubs. Positions and cash are never seeded by hand.

| Scenario | Required observation |
| --- | --- |
| Buy and sell, file-backed restart and in-memory repeat | NEXT_BAR/OPEN fills use raw 1000 rather than split-adjusted signal 100; costs reduce cash; fills, equity, portfolio state, economic state and provenance agree across replays. |
| Forward 10:1 and 2:1 split, reverse 1:10 split | Actual held quantity and entry/stop units convert once; an economically unchanged raw price transition does not create a stop fill or lose equity. |
| Split, ordinary dividend and simulated cash credit | Shares were actually bought before the ex-date; split precedes the dividend, receivable reflects eligible shares, later credit clears it once. No broker confirmation is claimed. |
| Partial GTD Bracket across the split | Parent fills on both sides of the event, child quantities protect actual converted exposure, historical fills retain their original share units, reservations and fee volume remain based on executed shares. |
| Genuine adverse price move | The live stop closes the actual long position, its OCO sibling cancels, no pending naked exposure or short position remains, cancelled protection has no fill or fee. |
| Cash sale and settlement | Friday sale proceeds remain unsettled and cannot fund another purchase; Monday's existing calendar settlement releases them, after which a new order can fill. No settlement algorithm is replaced. |
| Default missing evidence, short sale and time restrictions | Defaults reject missing earnings coverage; short sale and opening/closing buy buffers reject without fills, fees or exposure. A DAY order at the closed session raises the existing non-future-validity error before creating an order. |

The explicit positive-test configuration uses existing
`missing_earnings='allow'`, `missing_earnings_position='hold'` and
`unknown_industry='warn'`. These are disclosed, digest-bound opt-outs, not invented
earnings or sector evidence. A separate default-configuration case verifies
the existing fail-closed behavior. The cash settlement case uses an explicitly
bound exposure/reserve configuration to expose attempted proceeds reuse;
production defaults and account rules do not change.

The partial-fill test keeps the existing fractional-share commission treatment
(1% of executed value), distinct from the existing whole-share order minimum.
The tests assert actual fee records and journal fills; they do not substitute
a new fee, slippage or settlement calculation.

## Rejection and transaction boundaries

All nine core source roles are changed after a file-backed, order-capable plan
has been built. Both run and pre-run restore must reject before constructing
an Engine/Broker or invoking the strategy. Existing 5A/B/C/D/5E tests retain the
additional macro/tax/archive/receipt roles, identity, revision/tombstone,
coverage, symlink/junction/hardlink, alias and read-race cases.

Changing raw price/basis/share/volume units, the comparison signal or the volume
policy and recomputing a leaf/root cannot replace the externally held original
root. Even when a new root is explicitly supplied for the negative fixture,
the actual domain validator must reject the inconsistent bytes before Engine
allocation. A successful order-capable result, audit, handle or self-declared
trusted flag remains unusable at `build_research_engine`.

Fault injection is limited to the negative tests:

- After actual Engine construction, replace its feed/config or the bound input
  file. Final-use rejection leaves its complete economic state unchanged and
  does not call `on_start`, build an order or begin the Engine run.
- After prior successful fills, capture the complete economic state immediately
  before a bar. Inject a provider rejection, or call the actual split/credit
  processor and then raise. The existing transaction restores account state,
  cash, settled/unsettled/reserved cash, positions, pending orders, fills,
  trades, receivables, fee/slippage records, monthly fee volume, corporate state
  and the previous market clock. A diagnostic failure record may be appended.

The latter is **failed-bar atomicity**: prior successful bars remain committed.
It does not promise whole-backtest rollback. An already started or failed plan
cannot run again or produce a restart checkpoint. Restart remains an unstarted
plan's revalidated replay from zero, not live account/strategy recovery.

## CLI and fixed sibling compatibility

A fresh subprocess runs the documented
`python -m quant_constraints.research.data.entry_cli` command with two Python
hash seeds. Its successful synthetic output is deterministic and redacted;
verified-research and missing-mode requests return exit 2 without legacy
fallback. The no-order CLI probe is separate from the order-capable Python
tests above.

Module execution delegates to the canonical imported CLI implementation so
`_NoOrders` retains the same strategy module binding used when generating the
configuration digest. Previously the `__main__` module name caused the valid
documented synthetic command to return `CONFIG_BINDING_MISMATCH`.

`validation/test_pit_pr8_integration.py` retains immutable sibling SHA
`b5ecfd8527fe8eb6398a09b7f74e802e4324789c` and its process/import isolation. Added
actual Engine scenarios buy shares before the US 10% / Canada 15% synthetic
dividend scenarios, account for costs and credit the eligible net once. Actual
orders also exercise CPI deferral, later legal fill, FOMC risk sell and missing
macro-calendar rejection through the sibling's own gates.

These sibling tests do not merge PR #8 or install its policy in the new entry.
Macro/tax-enabled requests remain `RUNTIME_FEATURE_UNSUPPORTED` at the current
synthetic entry. Existing standalone macro/tax supply and ledger regressions
continue to run in the pinned integration.

## CI and retained limits

Full CI is triggered for the accepted 5E-3 stacked base; the existing complete
compatibility and Ecosystem matrices collect the new tests alongside the full
suite. All four coverage gates, the additive 94% statements / 85% branches
thresholds, frozen upstream evidence, documentation links, security checks and
reproducible wheel/sdist gates remain required.

This is still bounded synthetic input, single-security USD RTH 1m and immediate
completed-bar availability. It neither implements streaming years of minute
data nor broadens daily/delayed/ticker-transition support. No production trading
signals or cash/fee/slippage policies are added. Integrity cannot defend against
arbitrary hostile Python execution or establish source authority, licensing
or historical availability.

`raw_verified`, `PIT_verified`, `archive_ready`, `trusted_entry` and
`may_build_engine` remain false. Real Massive archives remain
`deferred_by_user / awaiting_paid_plan_and_license_review`; there is no real
issuer, Secret access, real download or real L0/L1/Parquet archive. Issue #5 and
real 5F admission remain blocked pending their own authorization and acceptance.
