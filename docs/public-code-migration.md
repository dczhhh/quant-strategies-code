# Public code migration

The public repository carries the complete reviewed source snapshot through 5F-4A,
including the upstream engine, cash-account constraints, offline data contracts,
documentation, packaging and the original CI jobs. The private source revision is
`e54913d2e7f10fb8d3caabe4955676326db3e1ee`, tree
`c397a263f4093831c6e4b222b8594390ac6912c2`. Existing source PRs remain open.

The public baseline is imported directly from the immutable public upstream commit
`c1ee19c385db3cbad225822cf845832470694f2b`. Only source files are added on top of a
new public history. No private Git commits, archive branches or reservation markers
are imported into public history. The MIT license and retained upstream evidence
are preserved. CI explicitly fetches that public commit for evidence checks.

## Public CI inputs

The real continuous-preparation metadata snapshot and the candidate-universe
table/source records remain private. Public CI uses
[invented metadata](examples/research-continuous-inputs-synthetic-v1.json).
The test harness temporarily binds the invented fixture hashes; production
fingerprints remain unchanged. The explicit offline check is:

```bash
uv run python validation/check_continuous_preparation.py --synthetic
```

Its output identifies `invented_public_fixture` and
`real_archive_metadata_verified=false`. It verifies preparation logic, not real
data or strategy performance. The default command still requires the original
private snapshot and fails closed when that input is absent or has changed.

## Private archive boundary

`dczhhh/quant-strategies` retains original evidence, issues, PRs and archive identity.
`dczhhh/quant-strategies-data` is the private destination for frozen archive bytes.
The [migration tool](private-archive-migration.md) uses repository-scoped secrets
only in the owner-triggered `private-archive` environment on a pinned `main` SHA.
Ordinary public CI has no archive credentials. Legacy acquisition workflows retain
their original private-repository gates and cannot acquire or publish from this
public repository. No production capture is enabled by importing these files.

Private migration failures report only a fixed phase and an allowlisted HTTP status
category. They never expose URLs, response bodies or credential values. Successful
readback compares original blob identities and SHA-256 records.
