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

Every public fixture is invented. This operator does not construct Engine or
issue a real/PIT certificate, does not authenticate a personal account replay,
and does not merge or unlock strategy PR #6. Its summary separates issuer
transport/schema, gross signal math, distribution classification, actual cash
reconciliation, and historical knowledge. Full real scenarios accepted remain
zero until the independent remaining evidence is supplied and checked.
