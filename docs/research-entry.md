# Research entry and synthetic runtime (5E-3)

This step follows [accepted 5E-2](https://github.com/dczhhh/quant-strategies/pull/15#pullrequestreview-5483851228)
at `8ab8c1fe02fddcc541d5baad424c5b6bac792a33`. It implements the opt-in build
boundary, an isolated synthetic runtime plan, bounded file admission, final-use
checks, restart validation and redacted provenance. It requires its own independent
acceptance before 5E-4's broader black-box and accounting E2E work begins.

**Issue #5 stays open. Real historical research is blocked.** No current issuer
can create a `VerifiedDatasetBundle`. `raw_verified`, `PIT_verified`,
`archive_ready`, `trusted_entry` and `may_build_engine` remain false in every
report, including successful synthetic execution. Real Massive archives remain
`deferred_by_user / awaiting_paid_plan_and_license_review`.

## Explicit build boundaries

The public APIs live in `quant_constraints.research.data.entry`:

| API | Current behavior |
| --- | --- |
| `build_research_engine(bundle, ...)` | The only new verified-research factory. Synthetic inputs reject with `SYNTHETIC_NOT_TRUSTED`; real declarations reject with `REAL_EVIDENCE_DEFERRED`. A manually allocated reserved bundle rejects with `TRUSTED_ENTRY_BLOCKED`. Dictionaries, reports, flags, lookalikes and synthetic handles reject with `INVALID_BUNDLE`. No Engine/Broker is constructed. |
| `synthetic_config_sha256(settings, strategy_type)` | Binds actual BacktestConfig, ConstraintConfig, strategy source identity and canonical explicit constructor parameters. This is configuration integrity, not strategy-policy approval. |
| `build_synthetic_test_engine(source, settings, strategy_type, reviewed_root_sha256=...)` | Runs the supported scope through accepted 5E-2 from original bytes, then returns a lazy single-use `SyntheticTestEngine` plan. It cannot enter the verified factory. |
| `plan.run()` | Revalidates source/root, config, strategy and software before allocation; checks again after strategy construction and immediately before Engine.run. Produces an existing BacktestResult with `metrics['data_provenance_v1']`. |
| `plan.checkpoint()` / `restore_synthetic_test_engine(...)` | Strict metadata-only restart of an unstarted plan, from independently retained inputs and config. Never adopts the root in the checkpoint as authority. |

`SyntheticRunSettings` copies the engine config and reconstructs constraint
settings. Parameters must be a bounded canonical JSON object encoded as bytes;
no pickle, plugin import, arbitrary account state or engine-factory callback is
accepted. Strategy classes derive from the existing `Strategy` API and must have
readable source. Caller-owned snapshot/config objects are copied; deliberately
altering the held plan is detected against its original bindings.

The synthetic plan is **not an Engine credential**. It constructs the original
DataFeed, Engine, ConstrainedBroker and SyntheticPITProvider; it does not implement
another broker, raw validator, ledger, fee/slippage model or corporate processor.
Legacy Engine/Broker/run_backtest and default behavior remain unchanged. The
mandatory boundary covers this new opt-in verified mode and its pipeline. A user
with arbitrary Python execution can still instantiate legacy Engine and alter
objects: Python privacy and digests cannot provide a process security boundary
or turn that execution into authenticated historical research.

## Supported synthetic runtime scope

This bounded harness supports one stable security, contemporary symbol, USD,
RTH 1-minute bars, immediate completed-bar availability and the existing strict
cash-account adapter. Execution uses NEXT_BAR/OPEN under the existing lifecycle;
completed raw bars reach decisions only at `available_at == bar_end_at`.
Comparison closes are supplied separately as `split_adjusted_close` signals;
raw OHLCV remains the execution/valuation/risk product. A delayed availability,
symbol transition or daily request rejects with `RUNTIME_SCOPE_UNSUPPORTED`.

The actual config must bind the request's `strategy_config_sha256`; altered
cash, execution, constraints, strategy parameters or source reject. Corporate
actions must be enabled and use the actual 5D provider. Its coverage and identity
are checked at each decision clock as well as the request asof, preventing a
later-known proof from being used earlier. Empty earnings information remains
unknown under the original configured missing-data policy; the harness does not
invent earnings or liquidity evidence to permit a buy.

Macro and dividend-tax runtime policies are currently rejected with
`RUNTIME_FEATURE_UNSUPPORTED`. The accepted 5E-2 verifier still supports their
offline evidence validation. This base does not contain PR #8's sibling runtime
gates/resolver, so a preflight report cannot pretend to install those policies.
The fixed PR #8 actual macro, tax and ledger integration continues separately at
`b5ecfd8527fe8eb6398a09b7f74e802e4324789c`. No sibling branch is merged or copied.

## Files, independent digests and final use

`entry_files.encode_file_manifest(snapshot, paths)` returns a
`research_input_files_v1` declaration manifest, without writing an archive.
Every source carries its existing metadata, digest, byte count and a unique
relative POSIX path. Original source bytes stay separate. `source_ref` remains
opaque and is never interpreted as a URL or filesystem path.

`load_dataset_files(root, manifest_path, reviewed_manifest_sha256=...,
reviewed_root_sha256=...)` requires both digests from outside the manifest and
checkpoint. A caller-chosen root still supplies only synthetic integrity, never
real authority. Use a dedicated, absolute canonical directory; explicit symlinks,
ancestor symlinks, Windows junctions, hardlink aliases, absolute leaf paths,
traversal, alternate separators and duplicate paths reject. On systems whose
temporary paths use OS aliases, the fixture owner must explicitly provide its
canonical directory before freezing the declaration.

Reads are bounded to the existing 2 MiB per source, 128 sources and 16 MiB total
snapshot limits. Each read checks a regular descriptor, path/descriptor identity,
size and SHA-256, with directory/descriptor/path checks before and after reading.
`O_NOFOLLOW` is used where available, with portable identity checks retained.
Cross-API stat/fstat comparisons use device/inode, size and modification time;
ctime is retained in both stat-to-stat and descriptor-to-descriptor change tokens,
without requiring the two APIs' ctime fields to have the same platform semantics.
The admitted immutable bytes supply the actual feed; execution never opens a
pathname again to fetch replacement data. Integrity re-reads at build, run,
after constructors and restart compare original manifest/root/file/directory
identities. Even replacement with identical bytes invalidates a held file plan.

The final feed's bytes, timestamp/cursor/spec/context binding and the Engine's
actual merged config are checked before `Engine.run` can call on_start, submit
orders, create receivables, process settlement, fees, fills or corporate actions.
A concurrent pathname change after the last check cannot switch execution to
new bytes: the feed already owns the verified immutable snapshot. These bounded
fixtures are not a streaming loader for years of real minute history.

## Restart and provenance

`synthetic_research_restart_v1` binds the original input root, verification audit,
config/strategy hash, entry version and source-tree hashes of ml4t.backtest,
quant_constraints and ml4t.specs. Restore reruns validation before any Engine
allocation and compares the entire canonical checkpoint. Old/missing/extra
fields, changed mode/root/config/software or altered source files reject.

Only **not-started replay from zero** is supported. There is no partial-run
account/position/order/strategy recovery, and a started or failed execution is
single-use. Arbitrary live account checkpoints are not accepted. Copy/pickle of
a plan rejects with `CHECKPOINT_REQUIRED`.

Successful execution reports `offline_entry_contract_pass` with
`engine_scope='isolated_synthetic_test'`, actual domain verification, original
source-role hashes/clocks/coverage, final feed digest and runtime software roots.
It inherits the accepted `data_provenance_v1` redaction and all false trust flags;
it contains no OHLCV, source paths/references, documents, Secret or tax identity.
Same frozen inputs, source code and actual config produce the same audit digest.
An input/config digest does not establish authority, licensing or strategy approval.

## Explicit CLI pipeline

The [5E-4 black-box acceptance suite](research-entry-e2e.md) extends this entry
with actual order/accounting, failure-atomicity, restart and CLI process checks;
it does not expand the synthetic runtime scope or issue real trust.

Run `python -m quant_constraints.research.data.entry_cli` with required `--mode`,
`--root`, `--manifest`, `--manifest-sha256` and `--input-root-sha256`. Mode
`verified_research` always rejects the current synthetic/unissued-real inputs;
there is no legacy fallback. Mode `synthetic_test` additionally requires
`--config` and `--config-sha256`, loading a strict `synthetic_research_config_v1`
object with `backtest` (BacktestConfig.to_dict) and `constraints` fields.

This CLI has a fixed no-order wiring probe and cannot import user trading
plugins. Bind its `_NoOrders` strategy in the synthetic request's config digest.
It emits only redacted provenance JSON, exit 0 on isolated success and exit 2 on
rejection (unissued real declarations retain `real_evidence_pending`);
invalid argument/config diagnostics do not echo paths or sensitive
values. No files are published as verified data and no real requests or Secrets
are read. Generic legacy result publication remains unverified.

Full CI/Ecosystem, the four existing coverage gates and pinned PR #8 integration
remain required. The three new entry modules each receive additive
**94% statements / 85% branches** gates. Passing this stage does not declare 5E-4
or real 5F E2E complete, authorize merging dependency PRs, or close Issue #5.
