# 2022 distribution evidence: fixed official-document capture

This is a separate, finite public-document collection phase under Issue 48. It is
not a strategy run or a replacement for the existing 48-product price-source plan.
The original source reservations and retained SPY/XLB bytes remain unchanged.

Before requesting any of these documents, freeze the source commit and plan hash:

1. SPDR S&P 500 ETF Trust FY2023 annual report, SEC accession
   `0001193125-23-285083`, document `d505786dn30d.htm`; candidate FY2022/FY2023
   tax-character evidence.
2. Select Sector SPDR Trust FY2022 annual report, SEC accession
   `0001193125-22-300047`, document `d403933dncsr.htm`; candidate FY2022 evidence.
3. Select Sector SPDR Trust FY2024 financial statements, SEC accession
   `0001193125-24-270452`, document `d870399dncsr.htm`; candidate comparative
   **FY2023** evidence, not evidence about FY2022.

Exact URLs, document IDs, scope, and the canonical plan hash live in
`tools/bear_2022_document_plan.py`. The fixed budget is three official HTTP
requests, zero market requests, and no market credentials. Only GitHub-hosted
Linux runners in the owner/main/private-archive context may collect them. Each
document has a private immutable reservation and result. An existing result is
read instead of requested again. Redirects, non-200 responses, timeouts, unknown
outcomes, and oversized responses stop collection without retries or fallbacks.

The HTML-document ceiling is 8 MiB; the existing private archive blob ceiling
remains 524288 bytes. Large HTML documents are retained as at most 16 separate
blobs. This does not increase any licensed-market response or GitHub client limit.
No subresources, scripts, links, cookies, or pagination are fetched. The capture
clock is current, never relabelled as historical knowledge.

The extractor locates public tax-character sections and retains their evidence
hashes. It does **not** automatically admit an ordinary-income classification.
Review must establish fund identity, fiscal-period coverage, all distribution
categories, and the absence or explicit treatment of capital gains/return of
capital. Calendar 2022 events can cross FY2022 and FY2023; the report year alone
does not classify an individual event. Issuer history and original-share
dividend/ex/record/pay data must also reconcile before economic replay.

A fresh read-only runner must rebuild identical evidence bytes without document
requests, market credentials, or private archive writes. Strategy defaults,
fees, slippage, historical T+2 settlement, decision timing, and ledger tolerances
are not modified by this collection phase. No 2022 performance claim is made.
