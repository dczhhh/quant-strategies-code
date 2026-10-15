# Reviewed retrospective rotation runner

This tool-only workflow does not merge strategy PR #6. It checks out the separate,
fixed strategy revision `eb70296eef3c79ed9bc259703504ed568e13b3ae`. That revision's
ordinary real entry remains blocked; only its explicit retrospective factory is used.
The workflow is manual, owner-only, exact-reviewed-main and GitHub-hosted only.
It retains the existing private-archive environment, disabled-debug checks and
per-write private-visibility checks. No capture runs on push or pull requests.

## Fixed data boundary

The 11 sector ETFs and SPY are the only targets. At most **48 new market requests**
are possible, four per ETF: original daily warm-up closes and original dividend/split
events for August 1, 2024–September 14, 2025, plus original minute OHLCV for
September 2–November 3, 2025. No pagination, redirect, automatic retry, additional
ticker, issuer-workbook request or limit increase is permitted. A timeout keeps its
reservation unknown; non-200 responses are retained but not admitted. Existing
inputs are reused before reading the market credential. Credential screening covers
the complete response before chunking into the unchanged 524,288-byte Git blobs;
the separate whole-response bound is 8 MiB. The shared 800-call GitHub cap remains.

Existing execution-window closes/events are bound to private raw-audit commit
`04f22aef8f5881bb1754edc9001c4ee59f36c0e2`, code `e0df084fbe316dbaf5db25de8516f0e20b606aca`,
and canonical manifest SHA256
`819bbdb8c04f244e25af999f352245ebf0c9aebb6228258a4ef9a0534af3a01c`.
The original unknown reservations and five vendor-derived conflicts are preserved.
The original `cash_amount`, never the conflicting current-share derived field,
is used with raw prices and effective share-unit changes.

## Independent stages

1. Twelve prepare jobs privately retain any missing fixed inputs and derive one
   immutable normalized component each. They reuse the retained issuer history and
   2025 Primary bytes; all execution-period events require an unambiguous identity,
   date and explicit ordinary-income-only match. Warm-up classification is not
   backfilled from a different tax year. Missing execution minutes are rejected.
2. Twelve fresh read-only jobs regenerate those components from the raw bytes,
   receipt chains and issuer documents. These jobs have no market key. Every
   regenerated component must equal its retained bytes.
3. After successful preparation and all independent checks, the pinned canonical
   Engine runs using the verified components. Its independent Decimal oracle checks
   cash, quantities, receivables, fees, modeled execution prices and settlement/NAV.
   Detailed inputs, observations and reports stay only in the private archive.
4. A fresh runner constructs a new Engine and repeats the replay read-only. Exact
   private result bytes must match, not merely a status string. It has no market key.

The replay stage trusts the preceding exact-source regeneration jobs for raw-to-
normalized derivation; it binds their immutable component bytes rather than loading
every raw response again in a single 800-call process. This trust boundary is explicit.
Only allowlisted status/count/source/hash fields are printed; no account values,
raw market rows, holdings, detailed trace or performance metric is a public artifact.

## Assumptions and acceptance

`config/rotation_research_2025.json` is a study definition, not a broker export.
It names 100,000 USD initial cash, zero initial holdings, a requested 10% withholding
scenario, modeled spread/slippage and minute participation. Fixed domestic ETF
identity is a disclosed research assumption, not historical PIT attestation. The
independent finite settlement list excludes October 13, 2025, based on the linked
NSCC notice; original PDF bytes were not retained. Pay-date credits and the existing
2026 fee snapshot backcast are modeled, not actual 2025 broker credits or bills.

`engine_invoked=true` and `RETROSPECTIVE_ENGINE_LEDGER_PASS`, if actually returned,
describe this conditional simulation only. Strict admission flags remain false and
`full_real_scenarios_accepted=0`. This does not explain the original workbook NAV
residual, certify investor qualification, resolve vendor-derived conflicts, prove
historical known-at, certify performance, or authorize strategy PR #6 to merge.

## Activation after tool CI and merge

Keep `REAL_ETF_PROBE_ENABLED=true`. Set `REAL_ETF_PROBE_REVIEWED_SHA` to the exact
reviewed merged **main** commit containing this tool. Manually run
**Reviewed 2025 retrospective rotation replay** on main. Existing `MASSIVE` and
`ARCHIVE_WRITE_TOKEN` environment secrets are reused, never copied into chat.
No replacement source workbook or new account export is required.

Offline operator tests use invented data only. CI additionally checks out the same
pinned strategy and requires a full invented 12-target normalize → Engine → fresh
private replay, with no source credential or real-data certification.
