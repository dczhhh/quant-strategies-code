# Public runner / private archive pull test v1

This operator tests the migrated execution path. It makes at most five fixed GET requests:

| Product | Scope |
| --- | --- |
| AAPL reference | Historical ticker detail for 2026-10-09 |
| AAPL raw and comparison | 2026-09-28, 13:30–20:00 UTC, one-minute bars |
| SPY raw and comparison | Same session and resolution |

Raw uses `adjusted=false`; comparison uses `adjusted=true`. Each aggregate response must contain
exactly 390 ordered regular-session bars, with valid OHLCV structure. The reference must identify
AAPL's active US common stock and large-cap classification. These are structural checks, not PIT
certification, independent corporate-action evidence, strategy results, or engine admission.
All existing admission flags remain false. Reference publication clocks remain unknown.

Execution requires the owner to manually dispatch the exact reviewed main commit in
`dczhhh/quant-strategies-code`, on a GitHub-hosted runner using the `private-archive` environment.
`PRIVATE_PULL_ENABLED=true` and `PRIVATE_PULL_REVIEWED_SHA` pin that commit. `MASSIVE` is passed
only to the capture step; `ARCHIVE_WRITE_TOKEN` writes only to the private
`dczhhh/quant-strategies-data` repository. The destination's identity and privacy are rechecked
before every Git write. No provider payloads, receipts, prices, artifacts, caches, or credentials
are published in the code repository or Actions logs.

Before each GET, a create-only private `research-reservations/pull-test-v1-<product>` ref durably
charges its single request. No automatic retry, redirect, pagination, additional ticker, or second
attempt is allowed. A reservation without a completed capture is an unknown outcome and stops
that product; it cannot reset the budget. A request has a 20-second timeout and a 512 KiB response
cap, making the plan's maximum response payload 2.5 MiB.

Each original response is immediately stored with a canonical receipt and checks in
`research-data/pull-test-v1-<product>`, at `research/data/pull_test_v1/<product>/`.
The capture commit has its reservation as its single parent; reservations are rooted in the
existing private bootstrap commit. Readback verifies exact tree membership and modes, Git blob
identity, original-byte SHA-256 and size, reservation ancestry, request scope, clocks, false flags,
and regenerated checks. Rejected HTTP responses and structurally invalid bodies are preserved
privately and then stop execution; credentials in a response prevent archival.

Already completed captures are checked before the provider key is accessed. A fresh runner repeats
the entire readback with zero GET requests to Massive and zero writes. Independently invoked
`verify` has no provider credential and cannot acquire missing products. Existing source archives
and their 26 budget reservations are unchanged. This September session must be reused if later
continuous-session work needs it; neither this operator nor the older private-repo operators
should be used to reacquire it.

The operator uses only Python's standard library. Local checks use invented responses:

```bash
python3 -m unittest tests.test_bounded_pull_test tests.test_private_archive_migration -v
```

Execution acceptance is pending: migration run `38398719090` stopped at the destination-inventory
read with `GITHUB_HTTP_401` before copying payloads or requesting provider data. The existing
`ARCHIVE_WRITE_TOKEN` environment secret must be replaced with a valid credential for the private
data repository (Contents read/write). The source token has not yet been exercised in this run.
Local invented-response success does not establish live provider or destination access.
