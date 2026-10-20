# Four-year registered-input inventory — no continuous backtest yet

This is the data-audit stage of [Issue 42's frozen plan](https://github.com/dczhhh/quant-strategies-code/issues/42#issuecomment-6262511098).
The execution-fix study at source `6c6e8e36799c788111c86b157d652a56ea7ec5c3`
is complete; its three independent windows are not spliced into a four-year result.
This change adds no provider acquisition executor, strategy, production default or Engine invocation.

## Evidence and boundaries

`tools/rotation_four_year_inventory.py` reads the three exact immutable normalized
input payloads from the completed execution-fix reports. It checks ref/commit pins,
Git blob hashes, manifest source/part paths, compressed and logical hashes and sizes.
It deliberately does not download or purport to revalidate every old Engine/report
or every minute-source body: those belong to the completed prior acceptance.

It also reopens all twelve 2022 source archives with the unchanged reader, including
their reservation/parent bindings, receipts, body hashes and secret-free checks.
All four raw products per ETF must still match the accepted normalized source hashes.
Their complete Massive split metadata spans 2021-10-19 through 2026-10-17, so it can
be reused for the entire four-year execution interval without another split request.

Six frozen ETF acquisition-plan families are reviewed: 2022 source, 2022 market,
2025 rotation, 2024–25 stress, 2025 ETF actions, and 2024 ETF actions. Known failed
planned slots are withheld too; recovery slots do not establish new date scope.
The private research-ref catalogue was inspected, but unrelated account/stock archives
are not searched. This is **not an entire-repository absence certificate**.

Actual complete rows, retained request scope and registered plan scope are separate:

- Each represented execution day needs all twelve ETFs and the original thirteen
  completed minute points, including actual NYSE half-day close.
- A requested-but-unrepresented row is reported as an anomaly, not rescheduled.
- Missing rows inside another registered plan are withheld pending raw/reservation
  reconciliation, rather than automatically requested again.
- Adjusted views and Yahoo monthly tail prices cannot replace original-share daily
  closes or minute fills. Overlap economics must agree; classification differences
  between a warmup row and an execution row never upgrade the former automatically.

## Read-only findings

The pinned inputs cover 442 of 1,003 expected NYSE execution days. Per-year expected
sessions are 251/250/252/250, including the January 9, 2025 exchange closure.

| Unrepresented execution interval | Trading days |
| --- | ---: |
| 2023-01-03–2024-11-29 | 481 |
| 2025-07-01–2025-08-28 | 42 |
| 2025-11-06–2025-12-31 | 38 |

Daily signal closes already cover 756 days. Only 247 need new registered scope:
209 on 2023-01-03–2023-10-31 and 38 on 2025-11-06–2025-12-31. Within the registered
scope, no requested-but-unrepresented daily/minute anomaly was found.

| Proposed missing-scope product | Requests across twelve ETFs |
| --- | ---: |
| Original-share minute bars, at most three consecutive required sessions per product | 2,256 |
| Original-share daily closes, two disjoint gaps per ETF | 24 |
| Original cash dividend events, two disjoint gaps per ETF | 24 |
| Split metadata | 0 |
| Total vendor products, not executed | 2,304 |

This is a finite proposed product list, not 2,304 requests already made or a mandate
to ignore provider permissions, old uncertain attempts, missing rows or write budgets.
Issuer classification and settlement-reference acquisition are separate work, not
included in this vendor-product count. A future capture executor must use create-only
reservations, bounded single attempts and private original responses/receipts, then
freeze and verify inputs before any continuous Engine run.

## Newly relevant corporate-action boundary

The reopened vendor metadata contains five 2-for-1 forward splits effective
2025-12-05: XLB, XLE, XLK, XLU and XLY. In unambiguous holdings notation, each old
share becomes two new shares (`new_per_old=2`).
The [issuer's November 20, 2025 notice](https://investors.statestreet.com/investor-news-events/press-releases/news-details/2025/State-Street-Investment-Management-Announces-Share-Splits-for-Five-Select-Sector-SPDR-ETFs/default.aspx)
independently identifies all five, December 2 record date, December 4 after-close
payment, December 5 post-split trading and unchanged CUSIPs. This inventory retains
that link as a reference, not as a separately archived original document or PIT permit.
The earlier three execution windows end before these splits.

The future continuous test must explicitly exercise quantity/basis conversion,
causal total-return continuity, pending intents, fees, cash and the independent
Decimal ledger across this boundary. The existing engine's split support is not
assumed to prove this new four-year scenario without the corresponding tests.

## Remaining admission work

The 2022 assumed-ordinary contract stays scoped to 2022. The old 2024 short-window
assumption is recorded as history, not silently extended. Same-year 2023/2024
classification and every 2025 event, including year-end, still need issuer evidence.
An event-year workbook's presence alone is not an event-level amount/date match.
Original-share cash amounts, ex/record/pay dates and the old 1E-8 evidence threshold
are unchanged. End-2025 receivables payable in 2026 remain receivables.

The independent settlement reference must span the full run and its final obligation,
honour the May 28, 2024 T+2-to-T+1 transition and distinguish trading from bank
holidays. Neither a union of short-window references nor the exchange calendar is
automatically a continuous settlement attestation.

The new M+T15R factor group and the five-group continuous matrix are not run here.
All report flags remain `engine_invoked=false`, `continuous_inputs_ready=false`,
`PIT_verified=false`, `performance_admission=false`. All new vendor/official capture
request counts are zero. No old archive is overwritten.

## Checks

```bash
uv run ruff check --no-fix tools/rotation_four_year_inventory.py tests/test_rotation_four_year_inventory.py
uv run ruff format --check tools/rotation_four_year_inventory.py tests/test_rotation_four_year_inventory.py
uv run ty check tools/rotation_four_year_inventory.py
uv run pytest tests/test_rotation_four_year_inventory.py -q --no-cov
```

Public tests use invented rows only. Real read-only inventory diagnostics use an
offline content-addressed adapter over connector-fetched private Git objects, with
no credentials or network entry point. Separate local hashseed processes can prove
determinism over that same retained snapshot; they do not constitute a fresh hosted
runner, new market acquisition, continuous input admission or four-year replay.
