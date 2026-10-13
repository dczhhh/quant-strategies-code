# Bounded on-demand capture and private archives (5F-1)

[5F-0 was independently accepted](https://github.com/dczhhh/quant-strategies/pull/18#pullrequestreview-5486001032)
at `72a215b5a3a1f8d88a5fdba7c117751274d7047c`.
The [5F-1 authorization](https://github.com/dczhhh/quant-strategies/issues/5#issuecomment-6116490564)
approves development for personal noncommercial, on-demand research and private
retention. The owner has already approved that activity category; account and
vendor rights, exact execution scope and technical acceptance remain separate.
The [5F-1 independent development review](https://github.com/dczhhh/quant-strategies/pull/19#pullrequestreview-5486454320)
accepted `f84115a47c84150651ade89563a494e3f6fa10ff`.
The [single-slice execution-review packet](research-slice-execution-review.md)
was [independently accepted](https://github.com/dczhhh/quant-strategies/pull/20#pullrequestreview-5486676084).
The [private local operator](research-private-capture.md) makes the next execution
path runnable, subject to actual private evidence and accepted activation.

**No real execution scope is registered.** No actual provider request, Secret
inspection, credential preflight or real archive has been performed. All five
`raw_verified/PIT_verified/archive_ready/trusted_entry/may_build_engine` flags
remain false. No issuer, backtest signal, account, fee, slippage or order policy
changes are included. Issue #5 stays open and all stacked PRs remain unmerged.

## Narrow demand contract

[`research_demand_capture_v1`](examples/research-demand-aapl-v1.json) binds a
capture ID, specific missing-evidence purpose, opaque private-root reference,
Massive/Starter selection, one symbol, one ended NYSE RTH session, UTC minute
boundaries and exactly two products: `raw` then `comparison`. The candidate is
AAPL 2026-10-09, 13:30–20:00 UTC (09:30–16:00 New York). This is a proposed
review scope, not evidence of actual prices, identity, permissions or access.

| Resource | Hard bound in this implementation |
| --- | --- |
| Scope | One symbol, one RTH session or a bounded portion of it; 1m only |
| Endpoints | Fixed HTTPS `api.massive.com/v2/aggs/ticker/.../range/1/minute/...` |
| Products | Explicit `adjusted=false` raw declaration and separate `adjusted=true` comparison |
| Requests | 2–8 total, including continuations and attempted requests |
| Original response | At most 512 KiB; total at most 4 MiB, limits can be lowered |
| Artifact inventory | At most 128 files, 2 MiB each, 16 MiB total |
| Retention | 1–30 proposed days; actual vendor/user-approved period must match review |
| Timeout and spacing | Timeout at most 30 seconds; explicit spacing 0–60 seconds |

The existing seven UTC days/2 MiB/source/128-source/16 MiB snapshot limits are
not raised. This first implementation is stricter. There is no symbol scanning,
whole-market loop, Flat Files, years of prefetch, daily request or automatically
started NFLX split slice. A future expanded backtest must still use correspondingly
verified data and accepted runtime support; capture scope does not expand the
current trusted entry.

The [provider aggregate specification](https://massive.com/docs/rest/stocks/aggregates/custom-bars)
explains split adjustment, millisecond window starts and base-aggregate limits.
The [pagination specification](https://massive.com/blog/api-pagination-patterns)
provides cursor background. These are public protocol references, not account
entitlement, license or raw-price authenticity proof. A separate 5F-1 URL scope checks the two product queries and reuses the existing
boundary-time decoder. All reviewed 5A–5E source files, including 5B's raw-only
`SourceRequest`, parser and licensed-cursor rejection, remain unchanged; pinned
implementation digests are not replaced or self-approved.

## L0, L1 and L2

| Layer | Stored private evidence | Meaning |
| --- | --- | --- |
| L0 | Exact original response bytes; separate credential-free request and receipt; product, URL, status, bounded byte count and actual capture clocks | Supplier declarations remain untrusted; no rewriting of raw/comparison responses |
| L1 | Separate Parquet preview per product, original-page digests, OHLCV and UTC interval, known ingestion clock | `provider_available_at`, `published_at`, `historically_known_at` stay null; identity/share/volume/correction verification stays unknown |
| L2 | `research_demand_archive_v1` manifest binding exact plan, request/response/receipt, Parquet, budget ledger and diagnostics, encoder/Polars version, acquisition/retention clocks and optional predecessor revision | Immutable integrity revision, never a `VerifiedDatasetBundle` or market/PIT certificate |

L1 is **not an execution feed**. Price basis is only provider-declared;
share/volume units are unknown until separately verified. There is no invented
`available_at`, no ingestion-to-historical-availability substitution, no filled
minute, interpolated price or inferred volume. Missing expected RTH minutes are
reported as unknown gaps, even for an exhausted empty response. Structural
OHLCV checks and successful pagination do not establish market completeness.
No independent factor, identity or historical PIT evidence is fabricated.

Source/comparison prices are not ratio-certified here. Vendor split metadata
and vendor raw/comparison are correlated sources. Independent issuer facts and
historical clocks remain a 5F-2 requirement. Macro/tax runtime and ordinary-dividend
qualification still require their previously specified evidence and acceptance.

## Append-only publication, re-entry and demand reuse

Private session paths derive from exact plan bytes. Request reservations are
written before transport; re-entry reuses complete, bound successful pages and
counts them in the same budget. A reservation without a complete validated
response/receipt stops with `INCOMPLETE_ATTEMPT_REVIEW_REQUIRED`, rather than
resetting a retry counter. Denials, redirects, rate limits and transport errors
stop immediately; automatic retry count is zero. Native partial-session restart
requires its own reviewed recovery scope and is currently rejected.

Only exact fixture byte mappings can enter `capture_fixture_slice`; callbacks
and lookalike objects are rejected. Fixture clocks/bytes and root identity bind
re-entry. Original bytes, separate product identities and clock order are
rechecked before deriving previews. Changed fixture binding, receipt, request,
source page or incomplete checkpoint cannot silently replace a prior capture.

Files use the existing exclusive `write_once` publication helper. The revision
manifest is the last commit marker; incomplete revisions cannot be loaded.
Readers require a separately held manifest SHA-256, exact canonical inventory,
roles, byte sizes and individual hashes. File descriptors reject aliases,
symlinks/junctions, hard links and replacement. Live-object manifest drift also
rejects. A self-chosen digest still does not confer source authenticity.

Checkpoint identity excludes read-induced access-time changes, while checking
device/inode, size, modification time, mode, link count, owner/group and change
time before/after the read. Change time is compared within each stat API, not
across Windows stat/fstat semantics. This permits unchanged APFS reads without
relaxing replacement, mutation, ownership or hard-link detection.

An explicitly known predecessor revision is fully audited before reuse. With
matching scope and no refresh reason, reuse performs zero new requests. A
refresh requires a named missing-evidence/correction reason and produces a new
revision linked to its predecessor. Diagnostics distinguish identical supplier
bytes from changed bytes; neither establishes a genuine historical correction
or its publication time. No silent refresh, deletion or overwrite occurs.

## Private root, stop conditions and credential boundary

The root must already exist, be absolute/canonical, outside a Git checkout and
free of path aliases. The implementation does not create or approve the root.
POSIX directories require owner-only permissions; new directories/files retain
0700/0600 permissions. Native operation additionally requires POSIX UID and
root inode binding. Windows fixture tests do not claim to certify Windows ACLs;
native operation on that platform is blocked pending a separately accepted ACL
implementation. Real cloud/CI access is not inferred from local permissions.

Expired retention prevents original-byte reads. A registered native archive
also requires its execution review to remain valid/not revoked and match the
plan and root; changing a live manifest cannot relabel it as a fixture to skip
that check. Actual termination/deletion obligations must be documented privately
before registration. This development stage deletes no user data and starts no
cleanup automation.

Native HTTP never redirects a bearer credential. It reads at most the remaining
body limit plus one detection byte; an oversize response is stopped and never
retained as a truncated success. Request budget includes every attempted request.
Safe outcome metadata records status/received length without error-body content.
Credential-bearing bytes/URLs are rejected before archival; headers and the key
never enter request/receipt/manifest/Parquet or public diagnostics. Failure
messages contain fixed codes/fields. No response, path or input IDs are printed
by these library entry points.

Before each native request and publication, review validity/revocation and root
identity are checked. 401/403, 429, redirects, unexpected status/schema/product,
endpoint/range/cursor drift, nonmonotonic clocks, duplicate bars, path/ACL drift,
request/byte exhaustion or sensitive data stops capture. Licensed opaque cursor
inheritance remains unverified and is rejected before the next request; synthetic
cursor tests do not authorize its use on real data.

## Execution review that is still required

`capture_real_slice` selects an entry from a deployed, reviewed execution registry.
The registry is **empty** in this PR. Unknown/self-selected hashes, approval
booleans and `OFFLINE_CONTRACT_PASS` cannot populate it or release a key.
Only after review may a subsequent narrowly scoped change register:

- Exact accepted implementation HEAD and plan-byte digest, named purpose,
  AAPL/single-session products/endpoints and all hard request/byte/time budgets.
- Independently checked current account/history entitlement and applicable
  internal algorithmic/non-display/private L0/L1/Parquet rights; private evidence
  references, retention/deletion obligations and review expiry/revocation rules.
- Actual canonical root path digest, root inode identity, owner/ACL evidence and
  approved local storage/deletion scope; no assumed permission for public CI,
  public artifacts, third-party processing or redistribution.
- Independently held execution-review digest and associated signoff. This is
  specific-slice technical/permission review, not another request for the owner's
  already granted willingness to conduct noncommercial, bounded research.

Before all those checks pass, the real entry rejects with
`EXECUTION_SCOPE_REVIEW_PENDING` **before** environment/credential/file/network
access. The old Basic preflight workflow is not dispatched or changed. There is
no new live workflow, automatic Secret probe or actual provider fixture in CI.

The native control flow is tested with an invented registry, fixture credential
and fake HTTP opener, all scoped to tests. No such entry is registered at runtime.
After eventual authorized acquisition, bytes remain untrusted until 5F-2 and
later actual raw/identity/PIT/accounting/runtime acceptance. Generic legacy
Engine APIs and arbitrary hostile Python execution are outside this integrity
boundary; this module does not secure an entire Python process.

The new per-module coverage gate requires 94% statements and 85% branches for
all three demand modules, in addition to the five existing coverage gates.
Full CI/Ecosystem, security, strict documentation, frozen evidence, reproducible
builds and pinned PR #8 integration remain required. Passing development checks
is not independent acceptance or permission to execute a real slice.
