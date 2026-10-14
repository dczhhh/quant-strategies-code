# Private issuer documents and ETF return probe

This operator acquires three fixed State Street issuer document URLs and reuses
36 individually pinned September 2024 captures covering the eleven sector ETFs
and SPY. It never reads a Massive credential or makes a new market-data request.
All source bytes and detailed calculations stay in `quant-strategies-data`.
Existing captures/reservations are read-only, including the recovered ETF group.

Issuer files have a separate single-attempt reservation before each request.
An unknown outcome cannot be reset. HTTP errors and redirects are archived as
unaccepted bytes; the operator does not follow redirects to another service or
silently replace failed documents. Workbook bytes are bounded at 8 MiB and
chunked into original 512 KiB Git blobs. The full concatenated source SHA256 and
source clock are checked after private readback. XML extraction is read-only,
rejects entities, duplicate members/cells, invalid dates and expansion above
32 MiB. Formula cached values are explicitly excluded from source facts.

The probe computes a **retrospective gross ex-close reinvestment hypothesis**
from original-share raw closes and distributions, then independently checks an
account of reinvested units. It verifies the split-only comparison scale over
the event window and converts distributions to the same share units. A
split-only price index must diverge from the dividend-inclusive series. None
of this assumes a real broker credit, actual reinvestment, a qualified tax rate,
or the issuer's NAV-based published fund return. Raw prices still feed execution;
a signal index is never added to the cash ledger.

The fixed plan includes a historical distribution workbook, the currently
linked ICI primary summary, and the legacy 2024 primary document URL. Workbook
header/year/ticker coverage is recorded separately from acceptance. A current
file covering a different tax year cannot classify a 2024 distribution, and a
year-end tax classification cannot be backdated to an earlier ex-date. Until
an independently verified row/date/identity mapping exists, classification
remains `ISSUER_CLASSIFICATION_MAPPING_UNVERIFIED` and unknown distributions
are not admitted to ordinary-dividend processing.

The source is manually dispatched only from public main at an exact reviewed
SHA, by the owner, on a GitHub-hosted runner using the existing private-archive
environment. The writer creates only new private Git objects. Same-runner and
fresh-runner verification recompute the whole report from archived bytes with
zero issuer/market requests. There is no public source-price artifact.

The October 14 real acquisition retained the historical workbook and current
ICI primary workbook with HTTP 200. The legacy 2024 URL has a reservation but
no captured response: its outcome remains unknown. The workflow now uses
`inspect` and `verify-inspection`, which make **zero source requests** and
validate that unknown reservation against the original fixed plan. Missing
captures remain explicitly absent; inspection neither fabricates HTTP status
nor resets/retries a request. It can still archive/recompute calculations from
the 36 completed inputs and inspect the two retained workbooks. This is an
inspection result, not a successful acquisition of the missing 2024 document.

Actual readback identified the historical `dividend` worksheet's exact
fund/ticker/CUSIP, ex/record/pay, dividend-dollar, short/long capital-gain and
frequency fields. The current primary file identifies tax year **2025** in
both its worksheet name and title. The January 2026 delivery clock is not a
tax-year or historical availability certificate for a 2024 event.

The field matcher requires that exact source header, one unique ticker/ex-date
row, a source fund name/CUSIP, exact record/pay dates and a dividend-field
difference no greater than `1E-8 USD`. It does not silently rescale a mismatch,
rename a capital gain as ordinary income, or treat blank gain fields as zero.
Source rows and differences stay private; the public summary reports counts
and outcomes only. A field match is retrospective, not an independently
established share-basis, ROC/special/final tax classification or cash admission.

Every public fixture is invented. This operator does not construct Engine or
issue a real/PIT certificate, does not authenticate a personal account replay,
and does not merge or unlock strategy PR #6. Its summary separates issuer
transport/schema, gross signal math, distribution classification, actual cash
reconciliation, and historical knowledge. Full real scenarios accepted remain
zero until the independent remaining evidence is supplied and checked.
