# Fixed 2022 minute/action capture, no economic replay

Issue 48's 12-asset source qualification must finish successfully before this
phase can run. Each asset must also independently rebuild its retained v2 source
audit at reader revision `94259b7687f453fbefa96a32d1f3a78506c6f9bf`. The original
48-product source plan, reservations, and raw responses are not replaced.

The new frozen market scope is exactly 1032 HTTP products: 86 per asset, split
into five independently reserved groups of at most 21 products. The first two
products per asset are (1) original-share dividends for 2020-11-02 through
2022-12-30, including explicit ex/record/pay fields, and (2) the still-missing
independent split reference from 2020-11-02 through 2021-10-18. The dividend and
split reference endpoints have all-history access under the existing Starter
plan; this does not extend aggregate-bar entitlement or authorize an upgrade.

The remaining 84 products per asset are unadjusted one-minute aggregates for
fixed consecutive blocks of at most three of the 251 execution sessions in
2022-01-03 through 2022-12-30. Bounds are each block's first market open through
last market close, with the existing 5000-row limit. No new daily aggregates,
alternate date windows, parameter search, pagination, retries, redirects,
fallback providers, or reservation resets are allowed. Original raw response
bytes are retained privately, never published as artifacts/logs or moved into
the local workspace. Non-200 or unknown outcomes stop and remain retained.

Every existing response/blob limit stays at 524288 bytes; groups have a separate
maximum of 32 compressed archive parts. Each process retains the unchanged 800
GitHub API call limit and paced writes. Existing complete groups are read instead
of requested again. A fresh runner reads without a market key, new market calls,
or archive writes. Credentials stay only in the approved private-archive runner.

This stage only retains inputs. It does not certify minute completeness, final
distribution tax classification, issuer-event reconciliation, independent
settlement calendar coverage, historical point-in-time knowledge, a strategy
decision oracle, economic ledger, or performance. All of these applicable gates
remain mandatory before the fixed ten-cell Engine comparison.

Primary endpoint specifications:
[dividends](https://massive.com/docs/rest/stocks/corporate-actions/dividends),
[splits](https://massive.com/docs/rest/stocks/corporate-actions/splits), and
[custom bars](https://massive.com/docs/rest/stocks/aggregates/custom-bars).
