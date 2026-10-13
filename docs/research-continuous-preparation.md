# Continuous AAPL preparation and bounded reference materials

This stage builds on accepted PR23 source `5d9444632a91de9e40748333c610642b401fac65`
and independent review `5498542402`. The owner separately authorized direct, necessary material
retrieval on 2026-10-13; no repeated category-level approval is required. Tests/CI must still pass,
the execution source must be pinned, and the repository must remain private. No trusted Engine or
strategy-performance permit is issued here.

## Fixed continuous scope

The byte-hash-frozen package resource `continuous_batch_v1.json` proposes exactly nine new AAPL
RTH sessions: September 28–30 and October 1–2, 5–8, 2026. Each session is 13:30–20:00 UTC,
390 possible minute bars, with raw and comparison products. That is **18 proposed aggregate GETs**,
not a market-wide scan. October 9 is reused from immutable commit
`0c35e58ada16a637955850602f0a9a84773c0ac6`, preserving its eight files and original source bytes.
The resulting intended window is ten consecutive sessions; the nine new sessions are **not yet
acquired** in this stage. Their coverage is unknown, not complete or automatically filled.

| Scope | Normal requests | Irreversible attempt cap | Maximum reserved response capacity |
| --- | ---: | ---: | ---: |
| AAPL reference, date 2026-10-09 | 1 | 2 | 1 MiB |
| Future nine-session aggregates | 18 | 20 (proposed, no executor here) | 10 MiB |
| Existing October 9 data | 0 | No new reservation | No re-download |

The material request is separate from the proposed aggregate budget. Neither namespace changes
first-batch `samples-v1` slots or PR22's original reservations. All responses are limited to
512 KiB, with 20-second timeouts, no automatic retry, no redirects, and no pagination.

## Why fresh classification materials are needed

The accepted first-batch AAPL evidence applies only to March 6/9, 2026; its cap basis is
March 28, 2025. It is outside the new session scope and 549 days before September 28, 2026.
The existing admission gate correctly rejects reuse. This stage does **not** broaden that frozen
gate or register self-signed instrument evidence.

Instead, the separate source-pinned private workflow queries only
`/v3/reference/tickers/AAPL?date=2026-10-09`, stores original bytes/receipt/audit, and checks
AAPL's expected CIK, common-stock type, US/USD/XNAS identity, active boolean and conservative
large-cap threshold. It never accepts an arbitrary ticker. This is retrospective acquisition
metadata, not independent identity or historical capitalization certification.

Massive's [reference documentation](https://massive.com/docs/rest/stocks/tickers/ticker-overview)
explains that the date comparison for SEC-derived facts uses the **report period**, not necessarily
the filing's publication date. A date parameter therefore cannot certify historical availability.
All three publication/availability clocks stay null and all five trust flags stay false.

## Cash-account and causal preparation

| Boundary | Preparation policy |
| --- | --- |
| September 30 → October 1 | Preserve positions, fees and receivables across month-end; no ledger reset |
| Friday October 2 sale | Settlement calendar returns October 5; do not release on the next minute |
| Friday October 9 sale | Settlement calendar returns October 13, outside the ten-session sample |
| Window end | Retain unsettled receivables; no invented settlement bar, forced release or liquidation |
| Future fills | Strictly after the completed decision bar; RTH only, long-only, settled-cash funding |
| Comparison series | Diagnostics only; not substituted for as-traded execution/signal units |

The [NYSE 2026 calendar](https://beta.nyse.com/publicdocs/nyse/ICE_NYSE_2026_Yearly_Trading_Calendar.pdf)
distinguishes trading sessions from bank/non-settlement holidays, including October 12.
The dates above are predictions from the existing calendar implementation, not a realized cash
ledger or a strategy backtest. Additional broker-specific settlement evidence, corporate actions,
units/volume, publication clocks, explicit fee/tax replay configuration and a bounded aggregate
executor remain technical work. Missing necessary materials may be obtained directly within scope;
these are not new requests for general owner approval.

## Checks and private execution

```bash
uv run python validation/check_continuous_preparation.py --synthetic
uv run pytest tests/constraints/test_continuous_preparation.py tests/contracts/test_continuous_coverage.py
```

The original fixed metadata-copy input remains private. Public CI uses an
[invented fixture](examples/research-continuous-inputs-synthetic-v1.json), temporarily binding
its hashes inside the test harness. Production fingerprints remain unchanged. The synthetic
result does not verify real archive metadata. Public CI never receives `massive`.

Actual material acquisition uses only `research-execution/continuous-materials-v1`, owner `dczhhh`,
explicit `MASSIVE_CONTINUOUS_ENABLED=true` and a matching `MASSIVE_CONTINUOUS_REVIEWED_SHA`.
The `massive` Secret appears only in the capture step; preflight and independent readback lack it.
The owner-only temporary root is outside checkout. Two create-only reservation refs survive new
VMs and retries. A verified archive is reused without reading the provider key or making another
Market GET. A partial same-root operation stops, rather than repeating an uncertain request.
Private Git stores the original response, receipt and non-PIT audit; Actions has no data artifacts.

This PR implements **reference acquisition plus continuous/E2E preparation**, not the nine-day
aggregate executor or a real-price strategy replay. `may_acquire_aggregates=false`, `may_replay=false`,
`engine_invoked=false`, and `raw_verified/PIT_verified/archive_ready/trusted_entry/may_build_engine=false`
are not overridden by a successful material GET or by the owner's direct-download authorization.
