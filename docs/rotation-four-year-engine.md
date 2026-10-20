# Continuous four-year controls

The frozen design runs M, M_T15R, T15_S, T15_SR and C15_S, plus SPY_PASSIVE,
SPY_SIGNAL_M and SPY_SIGNAL_T15R, at 0.1-share and whole-share steps. Each of
these sixteen accounts starts once with USD 100,000 on January 3, 2022 and runs
continuously through December 31, 2025. No annual reset or parameter/anchor search.
Monthly versus fifteen-session recovery produces two distinct matching SPY
controls; actual risk-state changes are the only SPY rebalancing signals.

The runner rebuilds all raw-backed inputs using frozen source
`ddaac328d7745324ff3aa864ef0fa39ad5d028e7` and compares every private input/audit
file against the already qualified archive before invoking any Engine. Original
strategy, execution, costs, numeric exit correction and independent Decimal
ledger tolerances apply. Every sector account also runs the independent signal
and submitted-intent oracle. Both SPY signal calendars are compared against the
corresponding sector reference on every available signal date.

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
