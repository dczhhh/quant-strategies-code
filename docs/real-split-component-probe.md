# Real captured split inputs: isolated component verification

This operator uses the existing private `real-case-supplement-v1` captures only.
It requests no market data and never prints original prices or response bodies.
Detailed daily values and expected/actual differences stay in a new immutable
private report; a second runner independently recomputes and compares it.

The tested production runtime is pinned to
`d2115c9780e9613b84e44939e25a7e7a2fc4a8cc`, plus four exact source hashes. This
does not merge the strategy branch into main. The capture source is pinned to
`c383bdab9661f9ebd2f18a5bb776194d871d64b0`; all 13 products are read back with
their original bytes, receipt, checks, budget parent and archive commit.

## Independent expectations and scope

The independent event terms are the ProShares 2024-10-28 announcement and
Form 8937, whose saved content hashes are pinned separately. First adjusted
trading is 2024-11-07, at one new share per five old shares. The sourced CUSIP
transition is 74347G432 to 74347G192. A lab identifier explicitly represents
that transition; it does not certify a full historical security master.

The position checkpoint is controlled: 50 old shares, basis at the captured
2024-11-06 raw close, zero execution fees, and controlled pending/protective
orders. It is not the user's holdings, an actual closing-price fill, or a
strategy recommendation. No order is submitted or executed for this leveraged
inverse ETF. A clearly marked, unapplied tax fixture enables the production
nonlegacy fractional rejection; it supplies no tax qualification or rate evidence.

Independent Decimal formulas expect 10 new shares, unchanged aggregate basis,
five times the per-share basis, unchanged cash and reservation notional, scaled
pending/protective quantities and prices, and scaled ATR/pending-exit units.
The native account is marked to actual daily raw closes from the checkpoint
onward. NAV equals controlled cash plus transformed shares times each raw
close; ordinary market movement is retained. Boundary continuity is checked
before the next actual mark. A 53-share case must reject cash-in-lieu before
any mutation; no cash price or tradable fraction is fabricated.

The independent adjusted series is checked using the documented cumulative
`historical_adjustment_factor` of the first later split. The query includes
later splits up to capture review day, so the end of the event window is not
silently treated as the adjustment anchor. This check is split-only, not total
return; it makes no assumption about adjusted volume or dividend compounding.

WOLF identity metadata is compared where stable identifiers are present. The
generic production reorganization-kind rejection is tested separately and is
not complete real WOLF acceptance. The original split response structure gap
and SEC 403 remain visible, and no exchange ratio is inferred.

## Causality and admission

The native adapter refuses daily OHLC execution. Therefore this operator calls
the split component directly in an isolated lab and uses account marking only;
it never manufactures intraday bars, a prepare/apply tuple, or PIT coverage.
It first checks that production `prepare` rejects `point_in_time=False` without
mutation. The event's availability is the actual capture clock, not a date
backdated from a publication, ex-date, or payable date. The later Form 8937
cannot serve as evidence of information available on the split day.

Every report remains `certification=UNVERIFIED`, historical known_at absent,
raw/PIT/archive/entry/Engine admission flags false, full real scenario accepted
false, and strategy performance computed false. Complete intraday account,
orders/fills/fees, fractional cash and historical-PIT acceptance are separate
requirements; PR #6 stays blocked.

## Operation

`real-split-probe.yml` is owner/main-only, with explicit enable and exact reviewed
operator SHA, using the existing private-archive environment without expanding
access. Dependencies and invented tests run without private credentials. Neither
job receives a provider key. The capture/helper plans and unknown reservations
are not changed. The report branch is create-only and scoped to operator SHA;
existing reports must match a deterministic recomputation exactly.

The public summary reports case status, counts and arithmetic residuals only.
There are no public data artifacts or broker account attachments. Fixture tests
are invented and never count toward real scenario acceptance.
