# Private first-batch samples

This stage stacks on the accepted [private GitHub archive](research-private-github.md).
It does not modify PR22's frozen acquisition channel or the AAPL 2026-10-09 archive.
Issue #5's current owner instruction authorizes category-level large-cap/ETF acquisition,
not a universe permanently limited to these four validation examples.

## Frozen first batch

| Symbol | Sessions | Products per session | Expected RTH minutes | Case |
|---|---|---|---|---|
| NFLX | 2025-11-14, 2025-11-17 | raw + comparison | 390 | 10:1 split |
| NVDA | 2024-06-07, 2024-06-10 | raw + comparison | 390 | 10:1 split |
| SPY | 2025-11-28 | raw + comparison | 210 | Early close |
| AAPL | 2026-03-06, 2026-03-09 | raw only | 390 | DST UTC shift |

The packaged `sample_batch_v1.json` is byte-hash frozen. Each symbol-day has its own
plan hash, source refs, bundle ref, paths and evidence digest. Twelve aggregate GETs
are expected. Fourteen irreversible global reservation slots bound the entire batch
to 7 MiB of worst-case response reservations; at most two manually initiated attempts
per product are possible. The two spare slots cover failures, not automatic retries
or reference calls. Each response is limited to 512 KiB, a 20-second timeout, one
request, no redirects and no pagination. No corporate-action API requests are needed:
reviewed public issuer summaries provide the two 10:1 reference cases.

## Instrument admission

`instrument_policy.py` checks reviewed symbol/identity, security type, USD cap basis,
cap as-of date and meme classification before reading a provider key or reusing cache.
Large-cap means approximately USD10 billion or more; actual ETFs are separately
admissible, not ETNs or closed-end funds. A 5% boundary buffer fails closed for review.
Small/micro-cap, confirmed memes, unknown identities and unreviewed evidence fail
closed. GME remains excluded even with a numerically large cap or an alias/class switch.
Mid-caps approximately USD2–10 billion need a substantive six-part rationale and a
source-pinned independent review bound to identity, evidence, exact plan and rationale.
The production mid-cap approval registry is empty; tests use explicitly invented reviews.

The four initial public fact summaries are source-linked in the frozen resource.
Apple, Netflix and NVIDIA use non-affiliate valuation lower bounds reported in SEC
filings; SPY uses issuer ETF identity. Dates and conservative valuation bounds are
explicit. These reviewed facts are retrospective acquisition classification, **not
exact daily historical capitalization or a PIT universe certificate**. In particular,
Netflix's 2025 annual filing was published after the sampled split dates. The actual
document bytes were not archived, so no document hash or exact availability time is
invented. Future category-admissible instruments require a new frozen evidence/scope
version and code acceptance, not a new blanket owner authorization.

## Private execution and partial recovery

The separate `private-samples.yml` runs only on the owner's source-pinned
`research-execution/samples-v1` branch, with `MASSIVE_SAMPLES_ENABLED=true` and
`MASSIVE_SAMPLES_REVIEWED_SHA` equal to the exact accepted source commit. It requires
GitHub-hosted runners and a private `dczhhh/quant-strategies` repository. Ordinary PR
CI never releases `secrets.massive`, captures real data or invokes this workflow.
Seven serial matrix jobs use independent fresh VMs; provider credentials appear only
in their capture steps. Owner-only ephemeral roots stay outside checkout. No Actions
artifact contains data; original bytes and derived files go only to private Git refs.

A successful product is immediately committed to its own two-file source branch
before the next product is requested. A later failure does not discard that response.
Fresh-runner recovery validates original bytes, receipt, Git parent, permanent
reservation refs, identity and scope before cache reuse. Reservations are never reset.
Same-VM partial retries stop. Existing source/data refs are create-only: conflicts,
extra paths, changed original bytes, metadata or binary encoders stop without overwrites.
Every bundle is reconstructed and byte-compared from its source-bound originals on reuse.

Each two-product slice has nine files; raw-only slices have six (57 derived files
across seven slices, plus 24 original-source files). CSV and Parquet schemas are explicit.
Missing bars produce a partial report with exact missing minutes; no rows are filled
and no session is forced to 390. Full first-batch coverage totals 4,320 product rows.

## Structural study, not certified strategy performance

The audit job uses no provider credential and records each input commit and manifest
hash. For split cases it compares the pre/post raw-to-comparison OHLC scale ratio
with the issuer's 10:1 summary, rather than assuming today's adjusted anchor. It
also checks DST session bounds and the 210-minute early close. A missing slice,
missing minute or conflicting split factor publishes an explicit partial/failed
audit and exits nonzero. Volume policy, price adjustment anchor, daily capitalization
and historical availability remain independently unverified.

Reports are immutable per run/attempt, under a dedicated private audit ref. All five
existing trust flags remain false; no trusted dataset, native permit, Engine run,
strategy performance or live trade is issued. This is an explicitly untrusted,
non-PIT exploratory structural study. The second AAPL multi-session batch remains
separate and must wait for independent first-batch acceptance; main is not merged.
