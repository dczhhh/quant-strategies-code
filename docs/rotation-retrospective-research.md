# Retrospective rotation execution

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
Canadian securities and every ADR remain excluded. This scenario supports whole-share ETF holdings
and the fee reference requires prices of at least one dollar. Fractional split outcomes fail closed;
ordinary fractional per-share dividend amounts remain supported.

## Execution assumptions

The research factory fixes next-bar execution at the **next completed minute close**, plus the stated
slippage. This permits completed-minute volume to be used without presenting it as opening liquidity.
Orders must predate fills. The normal 09:35–09:45 New York window, cash reserve, floating weights,
monthly plan and owner security restrictions are unchanged. The dataset requires every execution-
window minute, the first completed RTH minute and each session's closing mark; gaps are rejected.
Volume participation is capped by the explicit scenario and checked independently.

For each execution session the raw daily signal close and closing-minute mark must
agree within the retained source-rounding limit of 0.0002 USD. A larger difference
requires source reconciliation before Engine construction; the loader does not
choose a preferred price or adjust either series. This is a restriction of this
research path, not a claim that all vendors define daily and minute closes alike.
Action identifiers must be unique per security across splits, distributions and
generated modeled credits, so constructing the provider cannot overwrite an event.

Whole-share cash reservations reject a quantity that rounds to zero before asking
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
