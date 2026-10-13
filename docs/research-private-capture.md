# Private local capture operator

The owner's later [GitHub-hosted runtime authorization](https://github.com/dczhhh/quant-strategies/issues/5#issuecomment-6123926335)
now selects the [private GitHub archive workflow](research-private-github.md).
This local operator remains available, but WSL/local setup and its historical
pending evidence are not prerequisites for the new authorized GitHub job.

The [slice preparation is independently accepted](https://github.com/dczhhh/quant-strategies/pull/20#pullrequestreview-5486676084)
at `8c0c3adf6ddffe9b8a168c552e96b407feefc3c3`. The owner has approved completing
the implementation, then bounded real acquisition and research backtesting.
That intent does not need another approval. The accepted review still requires
actual account/license/root evidence, a bound execution permit and independent
acceptance of its activation before credential release.

This operator is runnable on an owner-controlled private POSIX host. It delegates
to the accepted capture implementation; it does not issue permits or real-data
trust. Its registry is currently empty. The local route is the implementation
default, not evidence that a host, account or root has been supplied or approved.

## Fixed scope

The operator encodes byte-for-byte the [accepted plan](examples/research-demand-aapl-v1.json):
SHA-256 `ab5de290a22b06f93299f9720d52a9ae2212d36b02c7ab476fe9b7e0b5052135`.
It has no ticker, URL, product, window, budget, retry or permission switches.
The historical opaque `storage_root_ref` stays unchanged; an eventual permit
separately binds the actual canonical root path and UID/device/inode identity.

| Scope | Bound value |
| --- | --- |
| Security/window | AAPL; 2026-10-09 13:30–20:00 UTC, end exclusive |
| Products | 1m explicit unadjusted raw, then split-adjusted comparison |
| Budgets | At most 8 attempts; 512 KiB/source; 4 MiB total |
| Timing | 20-second timeout; minimum 1-second interval; no automatic retry |
| Retention proposal | At most 30 days, subject to actual applicable terms and deletion review |
| Root | Owner-operated private POSIX root outside Git, canonical path, owner UID, directories 0700/files 0600 |
| Trust after capture | All five verification/archive/entry/Engine flags false |

The [execution-review packet](research-slice-execution-review.md) describes the
specific entitlement, use, retention, processor/location and deletion evidence.
Account identity and agreements belong in the private review, without an API key
or public source responses. A root hash authenticates no account or license.

## Run without releasing a key

Install the locked project environment on the selected private host with `uv sync
--dev --locked`. Supply the actual canonical root only after selecting its owner,
location, ACL and retention/deletion policy. The operator does not create or
choose that root. Do not substitute the development scratch directory.

```bash
export QUANT_CAPTURE_ROOT=/absolute/owner/private/archive
uv run python -m quant_constraints.research.data.private_capture_cli \
  --mode inspect-root --root "$QUANT_CAPTURE_ROOT"
```

`inspect-root` reads only metadata of the explicitly supplied local root. It
checks the existing private-root rules and native POSIX owner/permissions, then
reports two binding hashes and an observation time. It does not read archived
market data, inspect credentials, contact the provider or register a permit.
Success is `inspection_only / ROOT_INSPECTED_NOT_AUTHORIZED`, with
`may_acquire=false`. The private reviewer must authenticate the host, owner,
canonical path, identity, access/backup boundaries and policy behind the hashes.
Native Windows stops; WSL or a self-hosted runner needs its own actual review.

After a separately accepted activation registers the exact permit, set
`CAPTURE_ACCEPTED_REVIEW_SHA256` to that deployed review digest and run:

```bash
uv run python -m quant_constraints.research.data.private_capture_cli \
  --mode preflight --review-sha256 "$CAPTURE_ACCEPTED_REVIEW_SHA256"
```

Unknown/malformed/unregistered reviews stop **before credential or root
environment access and before root I/O**. Registered scopes require nonempty
private account/license references, an unexpired permit, the fixed plan digest,
supported POSIX, canonical owner-only root, both root bindings, and a second
registry/expiry check after inspection. Preflight never reads `MASSIVE_API_KEY`.
Success means `capture_preflight_pass / EXECUTION_SCOPE_REGISTERED` and
`may_acquire=true` for this bound capture, with `license_determination=not_performed`
and all trust flags false. It is not a new license determination or data review.

## Run the reviewed bounded acquisition

Only after that activation is accepted, supply `MASSIVE_API_KEY` privately in the
approved local operator environment. Do not put it in chat, Git, command-line
arguments, shell history, stdout or CI logs. The existing GitHub Secret `massive`
is not exported or probed by this operator. This change creates no public capture
workflow or Secret-consuming CI step.

```bash
uv run python -m quant_constraints.research.data.private_capture_cli \
  --mode capture --review-sha256 "$CAPTURE_ACCEPTED_REVIEW_SHA256"
```

The accepted native entry independently rechecks the scope/root before key access
and again before each request/publication. It preserves attempt reservation,
bounded reads, immutable cache, raw/comparison separation, exact-response L0,
untrusted Parquet L1 and manifest-last L2 publication. A complete unchanged cache
adds no requests. A partial native session requires review instead of a reset or
retry. Unexpected opaque pagination stops; empty/sparse/schema/permission/budget
failures never become completeness or raw-price proof.

The command emits fixed JSON audit codes. It never prints the root path, key,
response bytes/prices or source/manifest hashes. Success is
`captured_untrusted / CAPTURE_COMPLETE_UNTRUSTED`, with all five trust flags false;
blocked commands exit 2. Unrecognized backend diagnostics become a fixed stop
code. General interpreter/locale initialization is separate from the operator's
credential/root access boundary.

## Actual research entry remains a separate stage

```bash
uv run python -m quant_constraints.research.data.private_capture_cli \
  --mode backtest-status
```

This reports `blocked / REAL_BACKTEST_VERIFICATION_PENDING`, exit 2, without
credential/root access or archive reads. It does not run a backtest. Once a real
capture exists, the approved sequential plan still requires 5F-2 original-source,
identity and PIT verification, then 5F-3/4 actual research-entry and causal
accounting acceptance. A comparison response, simulated fixture or successful
archive cannot bypass that path.

Operator regressions use invented account/review/credential/root/transport facts;
no actual permit is created. CI adds a seventh independent coverage gate (94%
statements/85% branches) while preserving all six accepted gates and pinned PR #8
integration. This change does not alter the accepted capture modules, the plan,
strategy/broker rules or the prior-stage trust mechanisms. PRs remain unmerged
and Issue #5 remains open for the next independent acceptance and execution facts.
