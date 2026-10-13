"""Frozen synthetic 5B original-page/receipt/Parquet bindings through real 5B APIs."""

import hashlib
import json
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import timedelta
from pathlib import Path

import pytest
from test_research_archive import Clock, Transport, adapter, page, row, source_request
from test_verification_orchestration import fixture, freeze, rebind, rejected, run

from quant_constraints.research.data.archive import ArchiveInput, ArchiveStore
from quant_constraints.research.data.archive_contracts import canonical
from quant_constraints.research.data.datasets import iter_raw_bars, normalize_archived_snapshot
from quant_constraints.research.data.normalize import normalize_identities
from quant_constraints.research.data.verification_archive import encode_archive
from quant_constraints.research.data.verification_contracts import (
    EvidenceClocks,
    EvidenceRole,
    FrozenEvidence,
)


def archive_leaf(
    req, manifest, role, content, *, source_id, partition_id, source_ref, dependencies=()
):
    return FrozenEvidence(
        source_id=source_id,
        role=role,
        security_id=req.security_id,
        event_ids=(),
        source_ref=source_ref,
        provider=manifest.request.provider,
        dataset_id=manifest.request.dataset_id,
        dataset_revision=manifest.revision,
        partition_id=partition_id,
        version=1,
        window_start=req.window_start,
        window_end=req.window_end,
        clocks=EvidenceClocks(
            effective_at=None,
            historically_known_at=None,
            published_at=None,
            provider_available_at=None,
            ingested_at=manifest.acquired_end_at,
        ),
        depends_on=dependencies,
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
    )


def archive_fixture(tmp_path):
    req, records = fixture()
    request = source_request(
        security_id=req.security_id, start_at=req.window_start, end_at=req.window_end
    )
    clock = Clock()
    clock.at = req.window_end + timedelta(minutes=1)
    transport = Transport([page([row(at=req.window_start, o=1000, h=1010, l=990, c=1000, v=100)])])
    store = ArchiveStore(tmp_path / "offline-fixtures")
    source = adapter(transport, clock=clock).acquire_and_archive(
        request, store, normalize_identities(asdict(v) for v in records[EvidenceRole.IDENTITIES])
    )
    normalized = normalize_archived_snapshot(source, store)
    raw = tuple(iter_raw_bars(normalized))
    signal = replace(
        records[EvidenceRole.COMPARISON][0],
        source=replace(raw[0].source, record_id="comparison"),
        available_at=raw[0].available_at,
        raw_product_id=raw[0].product_id,
    )
    definition = replace(
        records[EvidenceRole.DEFINITION][0],
        provider=raw[0].source.provider,
        dataset_revision=raw[0].source.dataset_revision,
        raw_product_id=raw[0].product_id,
    )
    req = replace(
        req, raw_product_id=raw[0].product_id, asof=raw[0].available_at, archive_present=True
    )
    records[EvidenceRole.RAW] = raw
    records[EvidenceRole.COMPARISON] = (signal,)
    records[EvidenceRole.DEFINITION] = (definition,)
    license_leaf = archive_leaf(
        req,
        source.manifest,
        EvidenceRole.LICENSE,
        canonical(asdict(request.access)),
        source_id="license",
        partition_id="license",
        source_ref=request.access.license_ref,
    )
    packs = [
        archive_leaf(
            req,
            snapshot.manifest,
            EvidenceRole.ARCHIVE_MANIFEST,
            encode_archive(snapshot),
            source_id="archive-" + snapshot.manifest.layer,
            partition_id=snapshot.manifest.layer,
            source_ref="archive://" + snapshot.manifest.revision,
            dependencies=("license",) if snapshot is source else ("archive-source",),
        )
        for snapshot in (source, normalized)
    ]
    extra = [license_leaf, *packs]
    for snapshot in (source, normalized):
        for file in snapshot.manifest.files:
            role = {
                "response": EvidenceRole.L0_RESPONSE,
                "receipt": EvidenceRole.ACQUISITION_RECEIPT,
                "normalized_raw": EvidenceRole.L1_PARTITION,
            }.get(file.role)
            if role is None or (snapshot is normalized and role is not EvidenceRole.L1_PARTITION):
                continue
            extra.append(
                archive_leaf(
                    req,
                    snapshot.manifest,
                    role,
                    snapshot.read(file.path),
                    source_id=role.value + file.path,
                    partition_id=file.path,
                    source_ref=file.source_ref,
                    dependencies=("archive-" + snapshot.manifest.layer,),
                )
            )
    assert len(transport.calls) == 1  # A stub transport, never a live Massive request.
    return freeze(req, records, extra=tuple(extra)), source, normalized


def content_change(source, content):
    return replace(source, content=content, sha256=hashlib.sha256(content).hexdigest())


def test_real_5b_audit_normalization_and_parquet_replay_on_frozen_fixture(tmp_path):
    frozen, source, normalized = archive_fixture(tmp_path)
    report = run(frozen)
    assert report["status"] == "synthetic_contract_pass", report
    assert report["stages"]["archive"]["source_manifest_sha256"] == source.manifest.revision
    assert report["stages"]["archive"]["normalized_manifest_sha256"] == normalized.manifest.revision
    # Verification replays its own frozen bytes, never this subsequently changed fixture path.
    file = next(f for f in source.manifest.files if f.role == "response")
    (source.directory / file.path).write_bytes(b"path replaced after byte capture")
    assert run(frozen) == report


def test_os_temporary_alias_is_canonicalized_without_weakening_5b(tmp_path, monkeypatch):
    frozen, _, _ = archive_fixture(tmp_path)
    alias = tmp_path / "system-temp-alias"
    alias.mkdir()
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == alias or original_is_symlink(path))
    original_temporary_directory = tempfile.TemporaryDirectory
    created = []

    @contextmanager
    def system_temporary_alias(**kwargs):
        with original_temporary_directory(dir=tmp_path, **kwargs) as directory:
            created.append(Path(directory))
            # Emulate the OS alias ancestor on every platform without symlink privileges.
            yield str(alias / ".." / Path(directory).name)

    monkeypatch.setattr(tempfile, "TemporaryDirectory", system_temporary_alias)
    report = run(frozen)
    assert report["status"] == "synthetic_contract_pass", report
    assert created and all(not path.exists() for path in created)
    # Existing 5B admission still rejects a path with that symlink ancestor.
    from quant_constraints.research.data.archive import safe_path
    from quant_constraints.research.data.archive_contracts import ArchiveError

    with pytest.raises(ArchiveError) as error:
        safe_path(alias / "archive", "manifest.json")
    assert error.value.code == "PATH_INVALID"


@pytest.mark.parametrize(
    "role",
    [
        EvidenceRole.ARCHIVE_MANIFEST,
        EvidenceRole.L0_RESPONSE,
        EvidenceRole.ACQUISITION_RECEIPT,
        EvidenceRole.L1_PARTITION,
        EvidenceRole.LICENSE,
    ],
)
def test_archive_role_resealing_cannot_replace_reviewed_root(tmp_path, role):
    frozen, _, _ = archive_fixture(tmp_path)
    sources = tuple(
        content_change(s, s.content + b" ") if s.role is role else s for s in frozen.sources
    )
    rejected(
        run(rebind(frozen.request, sources), root=frozen.expected_root_sha256),
        "SOURCE_ROOT_MISMATCH",
        "binding",
    )


@pytest.mark.parametrize(
    "role", [EvidenceRole.L0_RESPONSE, EvidenceRole.ACQUISITION_RECEIPT, EvidenceRole.L1_PARTITION]
)
def test_rehashed_leaf_cannot_disagree_with_original_archive_pack(tmp_path, role):
    frozen, _, _ = archive_fixture(tmp_path)
    sources = tuple(
        content_change(s, s.content + b" ") if s.role is role else s for s in frozen.sources
    )
    rejected(run(rebind(frozen.request, sources)), "SOURCE_BINDING_MISMATCH", "archive")


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("missing-pack", "ARCHIVE_BINDING_MISSING"),
        ("same-layer", "ARCHIVE_BINDING_MISSING"),
        ("files-empty", "HASH_MISMATCH"),
        ("duplicate-file", "DUPLICATE_BINDING"),
        ("bad-hex", "INVALID_WIRE_VALUE"),
        ("file-hash", "HASH_MISMATCH"),
        ("schema", "UNSUPPORTED_PAYLOAD_SCHEMA"),
        ("real-access", "REAL_EVIDENCE_DEFERRED"),
        ("adapter", "VERSION_MISMATCH"),
        ("scope", "SOURCE_BINDING_MISMATCH"),
    ],
)
def test_archive_packs_require_complete_versioned_synthetic_bindings(tmp_path, mutation, code):
    frozen, _, _ = archive_fixture(tmp_path)
    sources = list(frozen.sources)
    index = next(i for i, s in enumerate(sources) if s.source_id == "archive-source")
    value = json.loads(sources[index].content)
    if mutation == "missing-pack":
        # Keep required role closure while removing the second distinct archive layer.
        sources = [
            replace(s, role=EvidenceRole.LICENSE) if s.source_id == "archive-normalized" else s
            for s in sources
        ]
    elif mutation == "same-layer":
        normalized_index = next(
            i for i, s in enumerate(sources) if s.source_id == "archive-normalized"
        )
        sources[normalized_index] = replace(
            sources[index], source_id="archive-normalized", depends_on=("archive-source",)
        )
    else:
        if mutation == "files-empty":
            value["files"] = []
        if mutation == "duplicate-file":
            value["files"].append(value["files"][0])
        if mutation == "bad-hex":
            value["files"][0]["hex"] = "xx"
        if mutation == "file-hash":
            value["files"][0]["hex"] = "00"
        if mutation == "schema":
            value["schema_version"] = "old"
        if mutation == "real-access":
            value["manifest"]["request"]["access"]["mode"] = "licensed_private"
        if mutation == "adapter":
            value["manifest"]["adapter_version"] = "massive_aggregates_v1"
        if mutation == "scope":
            value["manifest"]["request"]["symbol"] = "BBB"
        sources[index] = content_change(sources[index], canonical(value))
    rejected(run(rebind(frozen.request, tuple(sources))), code, "archive")


def test_genuine_5b_hash_valid_l1_still_requires_reproduced_derivation(tmp_path):
    frozen, source, normalized = archive_fixture(tmp_path)
    inputs = tuple(
        ArchiveInput(
            f.path,
            f.role,
            normalized.read(f.path) + b" " if f.role == "report" else normalized.read(f.path),
            f.source_ref,
        )
        for f in normalized.manifest.files
    )
    replacement = ArchiveStore(tmp_path / "changed-fixture").commit(
        request=normalized.manifest.request,
        layer="normalized",
        inputs=inputs,
        acquired_start_at=normalized.manifest.acquired_start_at,
        acquired_end_at=normalized.manifest.acquired_end_at,
        adapter_version=normalized.manifest.adapter_version,
        writer_version=normalized.manifest.writer_version,
        source_manifest_sha256=source.manifest.revision,
    )
    sources = tuple(
        replace(
            content_change(s, encode_archive(replacement)),
            dataset_revision=replacement.manifest.revision,
            source_ref="archive://" + replacement.manifest.revision,
        )
        if s.source_id == "archive-normalized"
        else replace(s, dataset_revision=replacement.manifest.revision)
        if s.role is EvidenceRole.L1_PARTITION
        else s
        for s in frozen.sources
    )
    rejected(run(rebind(frozen.request, sources)), "ARCHIVE_DERIVATION_MISMATCH", "archive")


def test_archive_and_5c_raw_are_the_same_canonical_values(tmp_path):
    frozen, _, _ = archive_fixture(tmp_path)
    sources = []
    for s in frozen.sources:
        if s.role is EvidenceRole.RAW:
            payload = json.loads(s.content)
            payload["records"][0]["close"] = 1001.0
            s = content_change(s, canonical(payload))
        sources.append(s)
    rejected(run(rebind(frozen.request, tuple(sources))), "ARCHIVE_RAW_BINDING", "archive")


def test_receipt_semantics_are_audited_by_5b_not_just_hashed(tmp_path):
    frozen, _, _ = archive_fixture(tmp_path)
    sources = list(frozen.sources)
    index = next(i for i, s in enumerate(sources) if s.source_id == "archive-source")
    packed = json.loads(sources[index].content)
    receipt_file = next(f for f in packed["manifest"]["files"] if f["role"] == "receipt")
    encoded = next(f for f in packed["files"] if f["path"] == receipt_file["path"])
    receipt = json.loads(bytes.fromhex(encoded["hex"]))
    receipt["status"] = 201
    changed = canonical(receipt)
    encoded["hex"] = changed.hex()
    receipt_file["sha256"] = hashlib.sha256(changed).hexdigest()
    receipt_file["size_bytes"] = len(changed)
    from quant_constraints.research.data.archive_contracts import ArchiveManifest

    manifest = ArchiveManifest.from_dict(packed["manifest"])
    sources[index] = replace(
        content_change(sources[index], canonical(packed)),
        dataset_revision=manifest.revision,
        source_ref="archive://" + manifest.revision,
    )
    sources = [
        replace(content_change(s, changed), dataset_revision=manifest.revision)
        if s.role is EvidenceRole.ACQUISITION_RECEIPT
        else replace(s, dataset_revision=manifest.revision)
        if s.role in {EvidenceRole.L0_RESPONSE, EvidenceRole.LICENSE}
        else s
        for s in sources
    ]
    rejected(run(rebind(frozen.request, tuple(sources))), "SOURCE_CONFLICT", "archive")


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("extra-license", "ARCHIVE_BINDING_MISSING"),
        ("wrong-license-ref", "SOURCE_BINDING_MISMATCH"),
        ("missing-license-edge", "SOURCE_DEPENDENCY_MISSING"),
        ("unmatched-leaf", "ARCHIVE_BINDING_MISSING"),
        ("orphan-leaf", "SOURCE_BINDING_MISMATCH"),
        ("bad-file-shape", "INVALID_WIRE_VALUE"),
        ("uppercase-hex", "INVALID_WIRE_VALUE"),
    ],
)
def test_archive_source_graph_and_inventory_are_exact(tmp_path, mutation, code):
    frozen, _, _ = archive_fixture(tmp_path)
    sources = list(frozen.sources)
    if mutation == "extra-license":
        sources.append(
            replace(
                next(s for s in sources if s.role is EvidenceRole.LICENSE),
                source_id="extra-license",
            )
        )
    if mutation == "wrong-license-ref":
        sources = [
            replace(s, source_ref="fixture:wrong-license") if s.role is EvidenceRole.LICENSE else s
            for s in sources
        ]
    if mutation == "missing-license-edge":
        sources = [
            replace(s, depends_on=()) if s.source_id == "archive-source" else s for s in sources
        ]
    if mutation == "unmatched-leaf":
        sources = [
            replace(s, partition_id="wrong-file") if s.role is EvidenceRole.L0_RESPONSE else s
            for s in sources
        ]
    if mutation == "orphan-leaf":
        sources.append(
            replace(
                next(s for s in sources if s.role is EvidenceRole.L0_RESPONSE),
                source_id="orphan",
                partition_id="extra-file",
            )
        )
    if mutation in {"bad-file-shape", "uppercase-hex"}:
        index = next(i for i, s in enumerate(sources) if s.source_id == "archive-source")
        packed = json.loads(sources[index].content)
        if mutation == "bad-file-shape":
            packed["files"][0] = ["private-malformed-file"]
        else:
            packed["files"][0]["hex"] = "AA"
        sources[index] = content_change(sources[index], canonical(packed))
    rejected(run(rebind(frozen.request, tuple(sources))), code, "archive")


def test_archive_encoder_does_not_accept_live_unknown_objects():
    from quant_constraints.research.data.verification_contracts import VerificationContractError

    with pytest.raises(VerificationContractError) as error:
        encode_archive(object())
    assert error.value.code == "REAL_EVIDENCE_DEFERRED"


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider", "wrong-provider"),
        ("dataset_id", "wrong-dataset"),
        ("version", 2),
        ("event_ids", ("unexpected-event",)),
        ("window_end", None),
    ],
)
def test_archive_leaf_metadata_is_bound_to_the_manifest(tmp_path, field, value):
    frozen, _, _ = archive_fixture(tmp_path)
    value = value or frozen.request.window_end + timedelta(minutes=1)
    sources = tuple(
        replace(s, **{field: value}) if s.role is EvidenceRole.L0_RESPONSE else s
        for s in frozen.sources
    )
    rejected(run(rebind(frozen.request, sources)), "SOURCE_BINDING_MISMATCH", "archive")
