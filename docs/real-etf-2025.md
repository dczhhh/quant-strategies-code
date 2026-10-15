# Same-year 2025 ETF source and return components

`tools/real_etf_2025.py` defines a separate immutable plan for the existing 11 sector ETFs and SPY.
Start with SPY; the `all` scope applies the same four-product plan to all 12 targets. Each ticker
has its own runner process, archive references and report, keeping the existing per-process GitHub
call budget. No original 2024 request, reservation, input commit or report is changed.

The fixed price interval is 2025-09-15 through 2025-11-03, covering the selected September event and
its payment boundary. The four bounded single-attempt requests per ticker are raw daily bars,
split-only adjusted daily bars, the new `/stocks/v1/dividends` schema and splits through the fixed
2026-10-15 anchor. This is at most 4 market requests for SPY or 48 for all existing ETF targets.
The split capture exists to check the current-share normalization anchor. Splits within the price
window are explicitly unsupported by this component probe; they are not silently normalized away.

Only GitHub-hosted runners on the reviewed exact main SHA may capture provider data. The new workflow
`real-etf-2025.yml` reuses `REAL_ETF_PROBE_ENABLED`, `REAL_ETF_PROBE_REVIEWED_SHA`, the `private-archive`
environment and existing credentials. It does not run on push or pull request, and shares the
existing corporate-action capture concurrency group. Matrix inputs enter shell commands through
quoted environment variables. Dependency installation and invented fixtures run before credentials
are exposed. Provider bodies, receipts and detailed reports are private only; there is no public
raw-data artifact. The fresh verifier receives no provider API key and cannot create missing input
or report references. Logs contain allowlisted aggregate statuses and a retained report digest.

The operator checks private visibility, preserves one reservation before each provider attempt,
retains non-200 responses without accepting them, scans credentials before archiving, verifies Git
blob hashes/parents/receipts, and does not reset an unknown outcome. A missing key is rejected before
reserving an unattempted request. Completed products are reused without obtaining the provider key.

Both issuer workbooks and their reservations are read at existing pinned commits. No issuer file is
downloaded again, and the unknown legacy 2024 source is not queried or reset. The bounded reader
checks the 2025 primary title and exact headers, then binds ticker/CUSIP/ex/record/pay to one unique
history event. Explicit component fields and their arithmetic are compared; blank prior/next-year
and E/R/C/X fields are preserved. A blank status is not a finality attestation. Qualified dividends
do not establish the account owner's withholding or treaty eligibility.

The new vendor fields have different purposes:

| Field | Use |
|---|---|
| `cash_amount` | Original per-share dividend for original raw-price share units |
| `split_adjusted_cash_amount` | Cross-check against captured later split ratios and adjusted bars |
| `historical_adjustment_factor` | Cumulative vendor normalization; not used as a second cash dividend |
| `distribution_type` | Recurrence category; never an ordinary/capital-gain/ROC tax classification |
| `declaration_date` | Event date; never manufactured into a timestamped historical known-at |

Sources: [new dividend schema](https://www.massive.com/docs/rest/stocks/corporate-actions/dividends),
[new split schema](https://www.massive.com/docs/rest/stocks/corporate-actions/splits).
Market JSON decimals are decoded without a float round-trip. Pagination, duplicate/ambiguous events,
wrong year/ticker/currency, missing amounts, malformed clocks and conflicting normalization fail closed.
Price-display comparison has a separate 0.0002 USD rounding allowance; issuer/dividend amount
comparisons retain 1E-8 USD, and independent signal arithmetic retains 1E-28 normalized units.
These are not changes to any account-cash reconciliation tolerance.

The gross return recurrence is checked against separate holding-unit reinvestment arithmetic.
Ex-date close reinvestment is a retrospective gross hypothesis, not an actual broker dividend
credit, net-tax return, DRIP, issuer NAV or formal Engine execution. Its signal never posts cash.
The same-year component result still leaves issuer finality, independent share-basis attestation,
historical PIT, withholding qualification, actual fills/fees/slippage and T+1 cash settlement
unverified. The source contains report-time information; final 2025 tax information published in
2026 must not be exposed to a 2025 decision. Every real entry/performance flag remains false,
`full_real_scenarios_accepted=0`; strategy PR #6 remains unmerged.

After this tool-only code is tested and merged, set the existing reviewed-SHA variable to that exact
main commit and manually run **Reviewed same-year 2025 ETF components**, first choosing `SPY`.
Inspect the component result as well as job success; denied or incomplete inputs can be retained
successfully while the component status is still unverified. After the SPY source path passes,
choose `all`; its completed SPY inputs are reused with zero additional SPY provider requests.
The capture job recomputes its private report, followed by an independent fresh-runner verifier.
