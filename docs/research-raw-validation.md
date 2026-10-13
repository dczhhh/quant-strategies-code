# Offline raw / split-adjusted validation (5C algorithm stage)

This opt-in module implements **synthetic-only 5C algorithm checks** on the existing
`research_data_v2` contracts. It is not a provider authentication service, PIT provider,
`VerifiedDataset`, permission to archive, or a trusted engine entry point.
**Issue #5 remains open and real historical research remains blocked.**

The owner has deferred real Massive L0/L1 archives until a future subscription notification
and separate license review. This implementation makes no API calls, reads no credential,
creates no files, alters no bars, and never calls a broker or engine. The already completed
12-case memory smoke is access/schema evidence, not a reproducible raw-price factor proof.
No market values have been reconstructed from its hashes or placed in fixtures.

## Interfaces and input binding

Import from `quant_constraints.research.data.raw_validation`:

- `ValidationInput`: immutable tuples of 5A `RawBar`, separate `SignalBar`, split
  `CorporateActionRecord`, `IdentityMap`, `SplitFact`, `FactorDefinition` and expected hashes.
- `input_hashes(input)`: six SHA-256 digests of actual canonical raw, comparison, actions,
  identity entries, facts (including document byte hashes/lengths), and definition.
  Freeze these **before** validating; a replaced record invalidates that binding.
- `RawOHLCVValidator().validate(input)`: a `ValidationResult` with deterministic status,
  code/field, combined input digest, source hashes, covered first/last bar and counts.
  `as_dict()` omits prices, volumes and source-document bodies.

Hashes protect against accidental replacement relative to the supplied binding. Rehashing
bad inputs is not source authentication. Frozen Python objects and a hand-written
`verified=True` are not security credentials; normalized contracts are reconstructed and
validated again. The source manifest and authority of real documents remain future work.

Only `input_kind="synthetic"` is accepted. A `licensed_private` (or other real-data) input is
rejected with `REAL_DATA_DEFERRED`. All results, including successful ones, retain
`raw_verified=false`, `PIT_verified=false`, `archive_ready=false`, `trusted_entry=false`.
The sole success status is `offline_algorithm_pass` / `SYNTHETIC_SPLIT_CONSISTENT`.
This result cannot be passed to any new engine-construction API: none is supplied here.

## Price and factor rules

The comparison must be a separate **split-adjusted**, not total-return, product tied to the
same raw product, stable security, symbol, listing identity, currency, interval, provider,
revision and adjustment version. Both products must have exactly the same timestamp set.
5A OHLCV, chronology, duplicate, source-record, identity and overlap checks are reused.

Each synthetic independent fact has exact bounded document bytes, SHA-256, source attribution,
event ID/version, first new-unit **trading date**, publication clock and integer new/old
share terms. Its canonical synthetic document must actually contain those declared terms.
The attributed source cannot be the bar provider; the action ratio/date/ID/version and
knowledge reference/clock must agree. Attribution is not proof of real independence or
authority. Revisions and cancellations reject in this stage, rather than choosing today's
final event version as a historical PIT snapshot.

For a bar before the first new-share trading session and events through the declared
adjustment anchor, `F = product(new_shares / old_shares)`. Check **each** O/H/L/C against
`adjusted_price = raw_price / F`. Reverse splits and multiple events use the same direction.
Events after the bar window but before the anchor matter too: declared event coverage must
start no later than the bar window and extend beyond the anchor. Coverage is not authenticated
by this algorithm. A window with no non-unit factor contrast rejects `NO_SPLIT_CONTRAST`,
not a claim that identical prices prove raw authenticity.

Calculations use a local 50-digit Decimal context. Tolerance is half the declared comparison
quantum plus half the raw quantum transformed by the factor. Price quantums are positive and
at most USD 0.01, and the combined tolerance must be less than 0.5% of the compared price.
These are bounded synthetic scope restrictions, not Massive's documented precision.

Volume policy is separate: either comparison volume remains **as-traded**, or it multiplies
the split factor and has adjusted-share units. Unknown policy, wrong units or inconsistent
values reject. Fractional and zero volume are not automatically forbidden. The synthetic
policies do **not** assert which policy Massive uses; that requires real source evidence.

## Sessions, knowledge and limits

- Single-security USD bundles, `1m` RTH or `1d` consolidated calendar-day bars, maximum
  31-day price window, 12,500 bars/product, 256 identity entries, 128 splits,
  16 KiB/fact document.
- Minute bars are aligned one-minute intervals inside actual NYSE RTH, including DST and
  early closes. Daily bars begin at **New York midnight**, end at the following local midnight,
  and do not imply RTH-only volume or intraday order timing.
- Missing RTH minutes are reported for the requested window, including zero-observation
  sessions; their reason stays unknown. No fill-forward or manufactured zero volume occurs.
- A fact's publication clock must match `historically_known_at` and be no later than the
  adjustment anchor. These are declared retrospective factor-version consistency checks;
  events after an individual raw bar are **not** made available to a historical strategy.
  Historical price availability and identity/PIT authenticity remain unverified; 5D must
  implement actual as-of snapshots and 5E must enforce the real research entry gate.
- Factor work is cached by trading day; bars are normalized once. No scan of account
  histories, snapshots, orders or positions is introduced.

## Regression and remaining work

Fixtures use invented `AAA` prices and `synthetic-A` IDs. The November 14/17, 2025 window
is an event-date analogue only, not copied NFLX market data. The independently published
NFLX dates are documented by the
[issuer announcement](https://ir.netflix.net/investor-news-and-events/financial-releases/press-release-details/2025/Netflix-Announces-Ten-For-One-Stock-Split/default.aspx)
and [SEC filing](https://www.sec.gov/Archives/edgar/data/1065280/000106528025000450/nflx-20251114.htm):
after-close legal/distribution dates are distinct from the November 17 first adjusted trading
session. This implementation does not claim to parse or authenticate either real document.

Tests cover forward/reverse/multiple splits, adjusted prices masquerading as raw after
rehashing, price and volume directions, identity conflicts, each hash role, replaced source
bytes, incorrect independent terms, missing/late knowledge, unsupported event revisions,
daily time basis, DST/half days, product isolation, no contrast and sanitized failure reports.
Rejected inputs never reach any new account-mutating API because this validator supplies
none. Enforcing validation before existing research-engine construction is a **5E task**,
not silently claimed complete by these pure-function regressions.

CI retains the original global/critical coverage checker byte-for-byte and adds a separate
5C gate requiring at least 94% statements and 85% branches in the new validator. Its own
tests reject absent, invalid or below-threshold evidence; existing thresholds are not reduced.

Real provider definition/precision/volume-policy and independently sourced factor evidence,
complete event coverage, source-parser binding, licensed reproducible archives, PIT identity,
macro/tax provenance, hash-bound entry and real E2E remain pending. Do not merge main or
close Issue #5 based on the synthetic pass.
