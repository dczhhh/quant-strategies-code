# Real company-action evidence acquisition, stage 1

This standalone acquisition change does not merge or admit strategy PR #6.
It expands the previously accepted five-request sample operator for the owner's
2026-10-14 authorization, without modifying old archives or resetting reservations.

## Exact capture scope

49 immutable products: NVDA June 2024 (6), GE July/August 2021 (5), a USD-listed
TD issuer/dividend input (2), and the 11 SPDR Select Sector ETFs plus SPY (36).
47 products use Massive; two are official issuer documents. Captures use independent
raw and split-adjusted daily requests. ETF windows are September 16–27, 2024;
dividend records use that same bounded ex-date window. Empty records are marked
structurally unaccepted, never proof of zero events or tax.

- NVDA official announcement separates June 6 record date, June 7 after-close
  distribution/effective terms and June 10 first split-adjusted trading session.
  [Issuer announcement](https://investor.nvidia.com/news/press-release-details/2024/NVIDIA-Announces-Financial-Results-for-First-Quarter-Fiscal-2025/).
- GE's 1:8 event separates July 30 legal completion and August 2 adjusted trading.
  Its terms prohibit issuing fractional shares and require cash-in-lieu.
  [Issuer completion announcement](https://www.ge.com/news/press-releases/ge-completes-one-for-eight-reverse-stock-split).
- Massive bars are split-only adjusted, not dividend-inclusive total return.
  [Provider product policy](https://massive.com/knowledge-base/article/is-massives-stock-data-adjusted-for-splits-or-dividends).
  Current-share-basis series fetched now are not historical PIT information.
- The current Starter OHLCV product has five years of history; the GE 2021 price
  window may be outside the account's product entitlement. The actual HTTP response
  is preserved and determines availability. Permission for more requests is not
  a paid-plan upgrade. [Provider pricing](https://massive.com/pricing?product=stocks).

## Privacy and execution

Only manually dispatched, owner-run, exact reviewed main code in the existing
main-only `private-archive` environment may use `MASSIVE` and `ARCHIVE_WRITE_TOKEN`.
No environment access expansion, local token extraction or PR-secret execution.
Raw bytes, response hashes, clocks, URL/product policy, source code SHA and
transport checks remain in private `dczhhh/quant-strategies-data`. No public
payload artifacts or caches, no arbitrary URL, redirect, pagination or retry.
Each finite product has a create-only durable reservation; unknown outcomes stop
without resetting old budgets. Existing products are verified before reading a
provider key, including on a fresh runner. Independent verify has no provider key
and uses a read-only archive client. HTTP-denied originals are retained privately
with unaccepted structure, not treated as success or substituted with mock prices.

After this standalone change is reviewed and merged, set `REAL_ACTION_ENABLED=true`
and `REAL_ACTION_REVIEWED_SHA` to the exact accepted main commit, then dispatch
`real-action-capture.yml`. The seven groups run serially; one group failure does
not cancel other groups. Both credentials already exist and their permissions
must not be broadened. PR #6 remains unmerged throughout input acquisition.

## Explicit remaining blockers

The workflow's success means capture/readback integrity only. Every case remains
`UNVERIFIED`; all raw/PIT/trusted-entry/engine-admission flags remain false.
Its invented transport tests count as zero real economic acceptance cases.
This is not the missing independent economic replay or real-tax certificate.

Still required for final PR #6 acceptance: archived fund-specific distribution
classification and dated independent issuer identity/country evidence for TD;
product policy/anchor and full relevant action coverage; independent expected
holdings, order, cost, cash, receivable and NAV reconciliation through the actual
broker; historical known-time/PIT evidence; and a privately supplied real IBKR
gross/withheld/fee/net/credit statement. A current ticker response's locale or
USD currency is not proof of tax-source country. No GE cash-in-lieu price, ETF
tax classification, treaty eligibility or actual broker credit is inferred.
