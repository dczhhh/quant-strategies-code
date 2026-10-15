# Unified acceptance after the owner scope change

Scope decision: 2026-10-14. The owner removed plan item 4 (Canadian tax and FX)
and excluded Canadian stocks and **all ADRs** from new positions, additions and
rebalance buys. This changes acquisition/trading scope, not evidence quality.

## Runtime policy

`config/us_cash_concentrated.yaml`, `config/cn_mainland_ordinary_dividend.yaml`
and `SectorRotationConfig.constraints()` enable `exclude_canada_and_adrs`.
The rotation factory rejects a controller disabling the policy. Every buy is
checked at submission and again before each fill, including plan-generated buys.
An explicit Canadian issuer or Canadian tax-source country rejects even a
USD-quoted, US-listed common stock. ADR/ADS and ADRC/ADRP/ADRW/ADRR/ADRU types,
or an explicit depositary receipt flag, reject new buys. The strict policy
requires visible, source-labelled identity metadata, matching symbol/stable ID,
domicile, tax-source country, and CS/ETF classification. Missing, conflicting or
future metadata rejects; listing locale and quote currency are not substitutes.

Sales and risk reductions still use the canonical account/session constraints.
The exclusion gate neither cancels dividend entitlements nor intercepts cash
credits. Existing excluded positions can exit and receive previously earned
dividends. No live brokerage order, instruction or DRIP setting is changed.

Generic `ConstraintConfig()` retains legacy compatibility for old callers and
invented fixtures. Explicit Canadian/ADR metadata is rejected even in that
mode. Its allowance of unknown legacy metadata is not the owner profile and
does not certify real data. Identity URL/hash/time declarations do not by
themselves establish source authenticity or historical PIT. The real rotation
entry and trusted dataset issuance remain blocked before Engine construction.

## One evidence matrix

| Item | Current state | Evidence or missing requirement |
|---|---|---|
| Entry exclusion | Code regression acceptance | Invented fixtures cover Canadian dual listings, ADR variants, unknown/future/cross-security metadata, additions/rebalances, fill revalidation, sales and credits after sale |
| SQQQ integer reverse split | `COMPONENT_MECHANICS_PASS` | Existing private probe [38538970239](https://github.com/dczhhh/quant-strategies-code/actions/runs/38538970239), private report commit `9832b574507d2de409097441be18b4786a370d0a`; this is controlled component execution, not an account E2E |
| SQQQ fractional cash | `UNSUPPORTED_CASH_IN_LIEU` | Rejection safety passed; actual fractional-cash price, quantity, charges and confirmed credit are absent |
| GE 2021 | `OUT_OF_WINDOW` / backlog | Outside current five-year provider bars coverage; no new fetch or waived fractional-cash evidence |
| WOLF | `UNSUPPORTED_REORGANIZATION_UNVERIFIED` | Cancellation/replacement/contingent distribution is not an ordinary split; retained vendor structural rejection and SEC 403 |
| Ordinary US dividend | Credit components pass; full lifecycle `UNVERIFIED` | Statement evidence supports private component reconciliation; independent ex/record/pay, pre-ex holdings/cash and complete trading/cash/NAV history are not all bound |
| ETF classification and total return | `UNVERIFIED` | Existing 11 sector ETFs + SPY raw/split-only/distribution captures are retained; official ordinary/capital-gain/ROC/special mapping and independent dividend-inclusive chain remain unverified |
| Historical PIT and intraday execution | `UNVERIFIED` | Present-day archived revision/capture time is not a historical knowledge clock; daily bars cannot establish intraday next-bar fills, costs or liquidity |
| US tax qualification | `UNVERIFIED` where personal eligibility is required | `qualification=null` continues rejecting estimated treaty benefits; actual sourced credit amounts do not infer eligibility |
| Canadian tax and associated FX workstream | `EXCLUDED_BY_OWNER_SCOPE` | Removed plan item 4; Canadian securities excluded from owner entry profiles, not marked tax-accepted |
| ADR entry/tax workstream | `EXCLUDED_BY_OWNER_SCOPE` | All ADR new buys excluded; existing ADR credits remain private historical component evidence, not eligible strategy purchases |
| Strategy return certification | `BLOCKED` | Requires remaining source/PIT, economic E2E and ETF acceptance; PR #6 remains unmerged |

The accepted SQQQ report hash remains
`7db6cd97c99b681f516da1cb5995e4aae9ab6b0a182292ccbd09fc2cb3179d6c`.
Five actual daily marks with controlled starting positions had quantity,
cost basis, cash and NAV error 0; unrealized P&L error was `1.3E-14 USD`
against `1E-8 USD` monetary tolerance. The probe and both readbacks made zero
new vendor requests. This source/pin change does not rerun or relabel that
prior result as execution on the new runtime.

The owner's broker connection was queried read-only for available trade/NAV
coverage. Current positions, currency balances, recent trades and daily NAV
are available; those APIs do not supply a complete historical cash/withholding
ledger or historical opening account state for the dividend case. No personal
account quantities, amounts, statement image or image fingerprint is published.
No earlier daily NAV is treated as an intraday ledger observation. Removing
Canadian FX does not certify a CNH-base account from USD ledger data.

Issuer documents located during this review:
[SPY fund and tax-document links](https://www.ssga.com/us/en/intermediary/etfs/state-street-spdr-sp-500-etf-trust-spy),
[historical distributions](https://www.ssga.com/library-content/products/fund-data/etfs/us/spdr-etf-historical-distributions.xlsx),
and [ICI primary tax summary](https://www.ssga.com/library-content/products/fund-docs/etfs/us/tax-documents/SPDR_ICI_Tax_Summary_Primary.xlsx).
Document location is not a content-hash-bound archive, a historical version or
classification acceptance. A primary year-end summary cannot be backdated to
the ex-date of an earlier distribution.

## Decision

Public regression success signs off the scope/exclusion implementation only.
Synthetic fixtures count as zero full real economic cases. The SQQQ component
pass is retained separately; full historical economic cases accepted remain
zero. Unified **real-corporate-actions / real-tax-reconciliation / strategy
certification is BLOCKED**, with the above gaps retained. No scope exclusion,
green CI, metadata string or unlimited API budget grants real-entry admission.

Legacy 5A–5E and tax source pins stay intact. A separately pinned entry overlay
used source commit `f38b3e8b75e1e555d3d66d8dad0b609371515e22` with verifier v5.
The additive research-isolation overlay uses frozen source
`0b2b716ed44e00f8a1027b6ef2ee3ca9257e60c6` with verifier v6. The additive
replay-integrity overlay uses source `4ccf9456d5d91efe1ff3fd718c7a06d18e6fdc1d`
with verifier v7, binding complete checkpoint coverage, order/fee identities,
cross-kind action uniqueness and raw daily/minute close consistency. The legacy,
tax, security and v6 overlay maps remain intact. Earlier v6/v5/v4 identities fail closed
instead of silently approving new source. Existing raw archives, receipts and
unknown request reservations remain unchanged. The separate
[retrospective research entry](rotation-retrospective-research.md) can invoke the
canonical Engine only under its explicitly modeled assumptions and independent
ledger checks; it grants no strict real/PIT certificate and does not close the
original account NAV discrepancy. No licensed market pull is performed by these
code changes.
