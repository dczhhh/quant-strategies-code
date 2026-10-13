# Private GitHub Actions acquisition and archive

The owner's [latest runtime authorization](https://github.com/dczhhh/quant-strategies/issues/5#issuecomment-6123926335)
selects GitHub-hosted Actions in the already-private repository. It supersedes the
earlier local/WSL-only execution boundary and additional lengthy questionnaires.
The local runner is not a prerequisite. This is owner authorization for personal
historical research, not a supplier-license determination or raw/PIT certification.

## Fixed execution and storage

`private-research.yml` runs only for the owner, the fixed
`research-execution/aapl-v1` branch and a source commit exactly matching
`MASSIVE_ARCHIVE_REVIEWED_SHA`. `MASSIVE_ARCHIVE_ENABLED` defaults to disabled
when unset. Neither PR events nor arbitrary inputs release the provider secret.
The private-repository check runs before the job and again before market requests
and every repository write. The job checks the explicit `runner.environment`
context, creates an ephemeral 0700 root outside Git, and uses locked dependencies.
`MASSIVE_API_KEY: ${{ secrets.massive }}` exists only in the capture step.
The upload step has no provider credential. No market data enters logs or CI artifacts.

| Bound | Value |
| --- | --- |
| Source | AAPL 2026-10-09, 13:30–20:00 UTC RTH, 1m unadjusted then split-adjusted |
| Plan bytes | Accepted SHA-256 `ab5de290a22b06f93299f9720d52a9ae2212d36b02c7ab476fe9b7e0b5052135` |
| Acquisition | Two fixed HTTPS requests, 20-second timeout, 1-second interval, no retries or pagination |
| Persistent allowance | Four atomic `research-reservations/aapl-v1-pair-{0..3}` refs, each reserving 2 attempts and 1 MiB conservatively; at most 8 attempts/4 MiB even across failed ephemeral jobs |
| File bounds | At most 512 KiB/file and 4 MiB/bundle |
| Data branch | `research-data/aapl-2026-10-09-v1` |
| Data directory | `research/data/aapl_2026_10_09_v1/` |
| Exact eight files | `raw_response.json`, `comparison_response.json`, raw/comparison CSV and Parquet, `checks.json`, `manifest.json` |

Reservations contain only a ref to the selected source commit; they do not copy
provider data, credentials, account records or local metadata. Unknown outcomes
remain conservatively charged. There is no automatic clearing/reset/deletion of
reservations, data refs or history. Concurrent runs are serialized; ref creation
is atomic. Manual attempts cannot exceed the four reservations.

The two exact HTTP response bodies are retained unchanged and checked for
credentials and unsupported free-text metadata. Their SHA-256 values bind the
CSV/Parquet derivation. Required checks include 390 ordered unique minute starts,
finite and valid OHLCV, declared products, original-response/table equality,
numeric schema and deterministic Parquet encoding. Duplicate JSON keys,
noncanonical/rebound metadata, extra files, malformed Git hashes and public
repository state stop publication. Git blob identities and the final data tree
are checked after creation. Only the fixed data paths can be added; billing,
agreements, host paths/UIDs, credentials and arbitrary directories are excluded.

The optional `count` field in the supplier's
[official stock aggregate fixture](https://github.com/massive-com/client-python/blob/master/test_rest/mocks/v2/aggs/ticker/AAPL/range/1/day/2005-04-01/2005-04-04.json)
must be an integer equal to `resultsCount` and the returned row count. Only this
validated alias is adapted for the unchanged native parser; the original HTTP
body, including `count` and its formatting, remains the hash-bound archive source.

The manifest records `owner_authorized_http_bytes`, observation time, fixed
request parameters and source/file hashes. Historical availability/publication
clocks remain unknown; units and adjustment are not independently certified.
All five trust flags remain false. Equal values in this single-day pair prove
neither split factors nor historical PIT. Source bodies are original received
HTTP bytes, not a legacy native 5F-1 manifest or a trusted dataset issuer.
The old execution registry and Engine admission gates are unchanged.

## Controlled activation

After code checks, bind the selected immutable commit in the repository variable
`MASSIVE_ARCHIVE_REVIEWED_SHA` and set `MASSIVE_ARCHIVE_ENABLED=true` for the
owner-authorized execution. Push that exact commit to
`research-execution/aapl-v1`. A push trigger is available without merging a new
workflow into main. GitHub's manual workflow registration requires the file on
the default branch; the implementation does not rely on an unavailable button.
No main or stacked dependency merge is performed.

The job runs `private_repo_ingest preflight`, `capture`, then `publish`. If the
private data branch already exists, it restores exactly those eight bounded
blobs, rechecks their contents and original-source linkage, and makes **zero new
market requests**. A changed/corrupt archive fails instead of being overwritten
or causing new downloads. Publication is create-only and never force-pushes.
Turning the enable variable off disables future executions; it does not delete
the already-approved private Git archive. Private Git history has no automatic
erasure guarantee, as recorded in the manifest.

Actual completion requires a successful source-bound run and a verified private
data commit. Code tests use invented credentials/responses and cannot establish
that a real fetch or upload has happened. Subsequent raw/identity/PIT and causal
Engine/backtest stages remain separately reviewable. This step adds no strategy
signals and permits no live trading or broad historical scan.
