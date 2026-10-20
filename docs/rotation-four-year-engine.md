# Continuous four-year controls

The frozen design runs M, M_T15R, T15_S, T15_SR and C15_S, plus SPY_PASSIVE,
SPY_SIGNAL_M and SPY_SIGNAL_T15R, at 0.1-share and whole-share steps. Each of
these sixteen accounts starts once with USD 100,000 on January 3, 2022 and runs
continuously through December 31, 2025. No annual reset or parameter/anchor search.
Monthly versus fifteen-session recovery produces two distinct matching SPY
controls; actual risk-state changes are the only SPY rebalancing signals.

The runner rebuilds all raw-backed inputs using frozen source
`e98a407ac4dca819fe60c7749904cb199a308e3f` and compares every private input/audit
file against the already qualified archive before invoking any Engine. Original
strategy, execution, costs, numeric exit correction and independent Decimal
ledger tolerances apply. Every sector account also runs the independent signal
and submitted-intent oracle. Both SPY signal calendars are compared against the
corresponding sector reference on every available signal date.

After the qualified archive is compared, an explicit Engine input view restores
only `bear2022:assumption:2022` to the provider's original `assumption:2022` ID.
The source must match the frozen 2022 policy hash and original source URL; all
affected events must remain in 2022. Only the source identifier and its tax-source
references change. Source bytes, observation clock, event identity, cash, dates,
classification, tax rate and every other input remain unchanged. The original
provider guard is preserved. The alias and Engine input fingerprint accompany
the report; the qualified input archive is never rewritten. Regression checks
compare all sixteen complete Engine results against the original canonical-ID
fixture and reject other years, sources, collisions and unrelated references.

## SPY recovery execution correction (report v2)

The original SPY control reused a monthly-only plan-creation gate. Fifteen-session
recovery signals could therefore be rejected solely for occurring outside the
monthly calendar. Recovery sizing also used total NAV, including an unpaid
dividend receivable. The cash guard correctly rejected an allocation that would
spend that receivable or consume the fixed cash reserve. These implementation
effects made the v1 SPY recovery controls unsuitable for the frozen comparison.

The isolated v2 adapter permits plan creation only during a validated, observed
recovery on its next execution session. It requires the exact SPY signal plan ID,
current recovered state and existing trading window. It cannot create calendar
top-ups. Before forwarding to the unchanged original plan manager, it sizes the
increment downward in the existing share quantum until the canonical account
guard accepts its estimated execution cost and fees. The requested target is an
upper bound; cash, reserves, settlement, dividend payment dates, fees, participation,
plan continuation and actual-fill checks retain their original rules. No unpaid
dividend is credited early. An independent signal reference checks every recovery
creation; the report records the sizing decisions and canonical cash snapshot.

Tests reproduce both the rejected non-monthly recovery and the unpaid-dividend
cash rejection. They require actual recovery buys, retain the receivable, reject
unobserved recovery authorization and compare downgrade-only 2022 results byte
for byte with the original controls. A real normalized-input diagnostic also
reproduces the first 2023 cash rejection and verifies a funded next-minute recovery
fill under the original account guard and independent ledger.

Source `20b5d6904c4ef8b0f7b7ac74d4973c3297df8c20` and its v1 report are superseded
for four-year performance comparisons. Corrected reports use the explicit
`rotation_four_year_engine_v2` prefix and create-only v2 ref; existing v1 bytes are
never overwritten. The qualified input source and input archive remain unchanged.

Complete orders, admissions, rejections, controller checks, fees, fills, actions,
monthly net trades, holdings, NAV paths and oracles are retained privately per
account. Year/quarter returns use the previous period's ending NAV; the first
period includes the initial day's PnL. CAGR uses inclusive calendar days and
365.25 days/year. Volatility and zero-cash-rate daily Sharpe use sample variance
and 252 sessions/year. Fixed 2x2 arithmetic return contrasts report both main
effects and their interaction. The original fixed 2025 selloff/rebound phase
boundaries remain reporting-only. No terminal liquidation or advanced dividend
payment is added; January 2026 SPY payable cash remains a year-end receivable.

2022 ordinary classification remains the owner assumption. The 2023/24 annual
fields come from explicitly labeled third-party archival issuer-workbook copies;
issuer-origin hash authenticity, finality and historical PIT remain unverified.
2025 retains the original issuer workbook checks. Fee snapshots are backcast,
with zero cash interest and modeled withholding; actual broker/performance/PIT
admission remains false. All source limitations accompany the comparison.

Before real operation, complete source CI, all twelve Ecosystem lanes and the
specialized sixteen-account checks must pass. Source stays Draft/unmerged.
A workflow-only launcher checks out the exact source with full history, requires
successful continuous input qualification, then runs seed 1 and a separate fresh
seed-33 complete raw-backed Engine reproduction. Only the first job can create
the private report; the verifier cannot write or request provider data. Unknown
reservations and quota limits retain their original stop behavior.
