# Issue #30: fixed stress-window evidence

The [fixed issue contract](https://github.com/dczhhh/quant-strategies-code/issues/30)
selects 2024-12-02 through 2025-06-30 before looking at its results. It does not
authorize tuning to this sample. The baseline is immutable source
`2b5c6029a6cd5fbb9e261cf5726836077b10039e`. G1 freezes current strategy settings,
model USD100000 cash, fees/spread/slippage/participation and whole-share quantum.
It does not silently introduce fractional shares or change the existing v4 report.

## G1: retained sources before new acquisition

`tools/rotation_stress_inventory.py` uses the established exact-source,
owner/main GitHub-hosted Linux context. Its [launcher template](examples/rotation-stress-inventory.yml)
has credential-free preflight, one private inventory writer and fresh read-only
verification. There is no provider key, provider request function, new official
request or Engine invocation. Raw inputs remain in the private data repository
and hosted runners; public logs contain only aggregate counts and flags.

The scope is the reviewed 2024 ETF source family, rotation-2025-v1 groups and
retained issuer documents, not unrelated private archives. Earlier 2024 raw/event
requests are contained in the rotation requests; their pins are recorded without
re-downloading redundant bodies. Later 2025 component dates and execution minutes
cannot supply unique coverage. Group tree paths, reservation parents, original
plan, product identities, body hashes/sizes and original observation clocks are
checked. Only the three relevant raw daily/dividend/split responses per asset are
downloaded, not later minute bodies or adjusted-price views.

The conservative daily range is 2023-11-01 through 2025-06-30: 415 exchange
sessions, including 272 presample and 143 execution sessions. A 252-day return
requires **253 close points**, not 252 prices. At least the closes from 2023-11-29
through the final presample close on 2024-11-29 are needed. The broader range
preserves the issue's requested conservative warm-up.

The existing 13 completed-minute observations are required for all 143 execution
sessions (1859 per asset), not just month starts: daily defensive changes and
delayed settlement funding can submit orders. The NYSE calendar includes half
days, DST and the 2025-01-09 funeral closure. Daily aggregates never substitute
for missing minutes. Three-session requests end at the last session's actual close
minus one millisecond; no unseen 16:00 bar is invented. This exchange calendar is
not historical NSCC settlement certification.

The report enumerates retained daily rows, missing dates, requested-but-absent row
anomalies, missing minute clocks, event-year issuer checks and a finite missing-input
proposal. Requested-but-absent rows are anomalies, not permission to re-request.
Daily gaps use exchange sessions; dividend/split coverage uses all calendar dates
so weekends cannot open unnoticed event-source gaps. No proposal is executed by
this operator. The hard proposal ceiling is 700 requests; the exact smaller count
is computed from retained coverage, not expanded automatically.

The outstanding 2024 issuer-document reservation is validated without reset,
retry or a fabricated HTTP result. A 2025 final tax document never classifies a
2024 distribution. Retained 2025 events are individually checked against same-year
history and existing ordinary-only research rules; failures remain visible. Any
assumed-classification scenario needs its own explicit versioned contract; G1 does
not grant it or strict cash admission.

`inventory.json.gz` and `manifest.json` are append-only under private ref
`research-data/rotation-stress-202412-202506-inventory-v1`. The manifest binds source
commit, LF-normalized operator SHA256, contract SHA256 and report hash. A fresh
runner rebuilds from retained bytes, compares every byte and cannot create a
missing report. Source-integrity failures prevent report creation. Reproducible
inventory with gaps does not make G1 input readiness complete.

## Remaining issue gates

G2 still needs complete inputs, a separately implemented signal/rank/target/order
oracle and frozen Engine/ledger checks. G3 needs an actual Engine replay and fresh
reconstruction; funded SPY and explicitly ex-post exposure-matched attribution;
monthly holdings, contributions, cash, fees and risk transitions; and beta/alpha
estimates with uncertainty. G1 supplies none of those returns or statistical
conclusions. Final classification, daily/minute source differences, settlement,
PIT and brokerage-confirmed credit limits remain visible. Production strict-real
stays blocked and strategy PR #6 stays open/unmerged. Owner exclusions and
Canadian/ADR entry restrictions are unchanged.
