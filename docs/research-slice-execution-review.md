# AAPL slice: execution-review preparation

[5F-1 development was independently accepted](https://github.com/dczhhh/quant-strategies/pull/19#pullrequestreview-5486454320)
at `f84115a47c84150651ade89563a494e3f6fa10ff`. The review permits preparation for
one actual slice, with execution still contingent on account/license/root and
independent scope review. This page and the [pending packet](examples/research-slice-preparation-v1.json)
make that proposal reviewable; they do not register an execution permit.

[This preparation was independently accepted](https://github.com/dczhhh/quant-strategies/pull/20#pullrequestreview-5486676084)
at `8c0c3adf6ddffe9b8a168c552e96b407feefc3c3`. The owner subsequently
approved completing the implementation and proceeding to real capture/research
afterward. The [private local operator](research-private-capture.md) now makes
the reviewed execution path runnable; actual evidence and activation remain
pending. No repeated general execution-intent approval is requested.

The owner's personal noncommercial, bounded download/private archive/research
intent is already approved. There is no request to repeat that general intent.
The four remaining factual inputs are account entitlement, applicable usage and
retention rights, the actual private supported environment/root, and the specific
execution review. No credential, account dashboard or actual private root has
been inspected; no market request or real archive has been created.

## Exact proposed slice

The [unchanged accepted demand plan](examples/research-demand-aapl-v1.json) has
SHA-256 `ab5de290a22b06f93299f9720d52a9ae2212d36b02c7ab476fe9b7e0b5052135`.
Changing root reference, scope, retention or budget changes that digest and
requires a new review packet. It is not silently changed to match a deployment.

| Field | Proposed value |
| --- | --- |
| Missing evidence | Original raw/comparison response pair and private archival control-flow smoke, not a trading signal |
| Security/window | AAPL; 2026-10-09, 13:30:00 to 20:00:00 UTC exclusive; one NYSE RTH session |
| Frequency/products | 1 minute; explicit unadjusted raw then split-adjusted comparison |
| Two initial requests | GET `/v2/aggs/ticker/AAPL/range/1/minute/1791552600000/1791575999999`, `adjusted=false/true&sort=asc&limit=5000` |
| Transport | Fixed `https://api.massive.com`; no redirects; 20-second timeout; 1-second minimum interval |
| Limits | At most 8 attempts including continuations; 524,288 response bytes/source, 4,194,304 total; one extra oversize detection byte, never accepted as truncated success |
| Retry/pagination | Zero automatic retries; native opaque cursor stops before continuation; no reset of partial-session budget |
| Proposed retention | At most 30 days, subject to an applicable permitted period and reviewed deletion policy |
| Environment | Owner-controlled private POSIX environment, not yet selected or verified; native Windows is unsupported |
| Credential | Existing GitHub Secret name `massive` is a reference only; no value/existence probe; eventual approved local operation needs owner-supplied private `MASSIVE_API_KEY` |
| Excluded requests | No account probe via market API, 1d/reference, NFLX, Flat Files, scanning, live quotes, public CI capture or unrelated backfill |

The two initial requests are not a promise of two-page completeness. An unexpected
cursor, exhausted budget, sparse/empty result, schema conflict or unavailable
permission stops the attempt. No missing minute or historical clock is invented.
An eventual successful capture stays untrusted, with all five trust flags false.

## Public references checked on 2026-10-12

These are public references, not an account-specific license determination.

| Official reference | Relevant observation | What remains unproved |
| --- | --- | --- |
| [Stocks plans](https://massive.com/stocks) | Starter lists 5 years, 15-minute delay and minute aggregates | This account's active plan and endpoint/history rights |
| [Custom Bars](https://massive.com/docs/rest/stocks/aggregates/custom-bars) | Supports millisecond bounds, explicit split adjustment, ascending sort and a base-aggregate limit (default 5000, maximum 50000); lists backtesting as a use case | Applicable account agreement, actual response/volume semantics, completeness and source truth |
| [Individuals Terms](https://massive.com/legal/individuals-terms-of-service), dated 2025-07-18 | Personal service terms incorporate market-data terms and applicable order/additional terms | Actual accepted agreement/order version and exceptions |
| [Market Data Terms](https://massive.com/legal/market-data-terms-of-service), dated 2025-08-28 | Sections 2 and 5 address display/non-display and derived investment-strategy permissions; section 8 addresses cessation/deletion after termination, restriction or suspension | Applicable permission for this personal algorithmic use and exact original/derived retention obligations |

**Inference for this proposal:** a documented API use case and noncommercial owner
intent do not identify the applicable non-display or retention permission for this
account. Do not label the proposed use forbidden or granted without the relevant
agreement or vendor clarification. Do not assume that delayed historical records
require every real-time exchange agreement; applicability must be resolved for
the exact product. No vendor contact has been sent.

## Inputs for the private reviewer

| Input | Minimum concrete evidence to bind | Current state |
| --- | --- | --- |
| Account | Dated active Starter subscription/order and endpoint/history/delay evidence for the two exact URLs; account identity kept privately, no API key | Pending |
| Use and retention | Applicable accepted terms/order/addenda or written vendor clarification covering personal algorithmic/non-display research, exact-response L0, derived L1/Parquet and proposed retention; conflicting terms resolved | Pending |
| Runtime/root | Actual owner-operated POSIX host and canonical root outside Git; UID/dev/inode/path digest, 0700/0600 owner-only permissions, authorized processors/location, backup/access boundaries | Pending |
| Deletion | Maximum permitted retention, earliest expiry, cessation/revocation and backup deletion obligations, responsible owner and metadata-only receipt; no deletion is run by this preparation | Pending |
| Execution review | Accepted implementation/plan byte digests, reviewed private evidence references, exact root bindings, named operator, approved budgets, validity/revocation and independently held review digest | Pending |

Account receipts and license text are evidence to authenticate privately, not
public Git content. A root label or hash alone does not authenticate its ACL or
owner. This cloud scratch workspace is not automatically an approved private
archive. Do not fill root bindings from it merely because it is POSIX. A Windows
host would require separately accepted native ACL support or an explicitly
reviewed private POSIX environment; WSL is not presumed approved.

The [GitHub Secrets API](https://docs.github.com/en/rest/actions/secrets#get-a-repository-secret)
returns metadata without revealing the secret value. The preparation
does not export a Secret into a local host. A local operator needs an independently
approved private credential-supply method; a CI-based operator would instead need
separately reviewed cloud/storage/processor permissions. Neither is assumed here.

If clarification is needed, the following questions are ready to use with the
vendor, but have not been sent:

1. Does my current Stocks Starter agreement permit only-myself noncommercial
   algorithmic backtesting/non-display investment-strategy research using this
   one AAPL historical 1m unadjusted/adjusted slice? Which term/order/addendum
   supplies that permission and which provider requirements apply?
2. May I retain the exact response bytes and derived private Parquet locally for
   up to 30 days? What retention/backup and termination, suspension or restriction
   deletion obligations apply? No redistribution, public CI or third-party access
   is requested.
3. Is this active account entitled to the two listed historical aggregate requests,
   with the stated products/window? Clarification must not require publishing a
   key or running an unreviewed market-data probe.

## Offline consistency check and next review

```bash
uv run python validation/check_research_slice_preparation.py
```

The checker reads only fixed repository packet/plan/source files. It binds the
accepted capture modules and unchanged plan, exact proposed requests and budget,
owner approval and pending factual inputs. It neither resolves private references
nor accesses environment/Secret, socket, provider or private archive paths. It
does not update `_ACCEPTED_EXECUTION_REVIEWS`.

A successful exit means **PREPARATION_ONLY / EXECUTION_REVIEW_PENDING**;
`may_acquire=false`, `license_determination=not_performed` and all five trust flags
remain false. This packet version cannot accept an approval boolean, populated
execution digest, real path or arbitrary request. It is a draft consistency check,
not a permission mechanism. Source/plan drift or malformed data fails closed with
fixed diagnostics, without echoing metadata values or credentials.

Next, authenticate the private inputs and independently accept the exact
activation scope and implementation. Only a subsequently reviewed narrow change
may register the accepted `_ExecutionPermit`; no PR, report or JSON self-grant can
release the credential. Do not restart a partial native capture or expand the
slice to repair missing evidence without another bounded review.

5F-2 raw/identity/PIT and later actual research-entry/runtime validation remain
separate after any authorized acquisition. PRs stay unmerged and Issue #5 stays
open. This preparation changes no accepted capture/validator/strategy/broker code.
