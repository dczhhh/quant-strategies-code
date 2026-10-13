"""Reuse 5B on bounded frozen synthetic archive bytes in a temporary workspace.

No incoming pathname is opened during verification, no network is used, and no
real archive is acquired or published. This is not the 5E-3 streaming loader.
"""

from __future__ import annotations

import tempfile
from dataclasses import asdict
from pathlib import Path

from .archive import ArchivedSnapshot, ArchiveStore, audit_archive, safe_path
from .archive_contracts import (
    ADAPTER_VERSION,
    AccessDeclaration,
    ArchiveManifest,
    canonical,
    digest,
)
from .datasets import iter_raw_bars, normalize_archived_snapshot
from .raw_validation import _canonical
from .verification_contracts import EvidenceRole, FrozenEvidence, ResearchRequest, _deny
from .verification_wire import decode_json

ARCHIVE_PAYLOAD_VERSION = "research_verification_archive_payload_v1"


def encode_archive(snapshot: ArchivedSnapshot) -> bytes:
    """Transport an existing synthetic 5B fixture; this does not acquire data."""
    if (
        type(snapshot) is not ArchivedSnapshot
        or snapshot.manifest.request.access.mode != "synthetic"
    ):
        _deny("REAL_EVIDENCE_DEFERRED", "archive", "archive")
    audit_archive(snapshot)
    return canonical(
        {
            "schema_version": ARCHIVE_PAYLOAD_VERSION,
            "manifest": asdict(snapshot.manifest),
            "files": [
                {"path": f.path, "hex": snapshot.read(f.path).hex()}
                for f in snapshot.manifest.files
            ],
        }
    )


def _unpack(
    source: FrozenEvidence, request: ResearchRequest
) -> tuple[ArchiveManifest, dict[str, bytes]]:
    value = decode_json(source.content)
    if (
        type(value) is not dict
        or set(value) != {"schema_version", "manifest", "files"}
        or value["schema_version"] != ARCHIVE_PAYLOAD_VERSION
        or type(value["files"]) is not list
    ):
        _deny("UNSUPPORTED_PAYLOAD_SCHEMA", "archive", "archive")
    manifest = ArchiveManifest.from_dict(value["manifest"])
    declared = manifest.request
    if declared.access.mode != "synthetic":
        _deny("REAL_EVIDENCE_DEFERRED", "archive", "archive")
    if manifest.adapter_version != ADAPTER_VERSION:
        _deny("VERSION_MISMATCH", "adapter", "archive")
    if (
        any(
            getattr(declared, n) != getattr(request, n)
            for n in ("security_id", "symbol", "venue", "currency", "frequency", "session_filter")
        )
        or declared.start_at != request.window_start
        or declared.end_at != request.window_end
        or source.provider != declared.provider
        or source.dataset_id != declared.dataset_id
        or source.dataset_revision != manifest.revision
        or source.partition_id != manifest.layer
        or source.source_ref != "archive://" + manifest.revision
        or source.window_start != request.window_start
        or source.window_end != request.window_end
        or source.version != 1
        or source.event_ids
        or source.clocks.ingested_at != manifest.acquired_end_at
        or any(
            getattr(source.clocks, n) is not None
            for n in (
                "effective_at",
                "historically_known_at",
                "published_at",
                "provider_available_at",
            )
        )
    ):
        _deny("SOURCE_BINDING_MISMATCH", "archive", "archive")
    files = {}
    for item in value["files"]:
        if (
            type(item) is not dict
            or set(item) != {"path", "hex"}
            or type(item["path"]) is not str
            or type(item["hex"]) is not str
        ):
            _deny("INVALID_WIRE_VALUE", "archive", "archive")
        if item["path"] in files:
            _deny("DUPLICATE_BINDING", "files", "archive")
        try:
            content = bytes.fromhex(item["hex"])
        except ValueError:
            _deny("INVALID_WIRE_VALUE", "archive", "archive")
        if content.hex() != item["hex"]:
            _deny("INVALID_WIRE_VALUE", "archive", "archive")
        files[item["path"]] = content
    if set(files) != {f.path for f in manifest.files}:
        _deny("HASH_MISMATCH", "files", "archive")
    for item in manifest.files:
        if digest(files[item.path]) != item.sha256 or len(files[item.path]) != item.size_bytes:
            _deny("HASH_MISMATCH", "files", "archive")
    return manifest, files


def _materialize(
    root: Path, manifest: ArchiveManifest, files: dict[str, bytes]
) -> ArchivedSnapshot:
    directory = root / manifest.revision
    directory.mkdir()
    encoded = canonical(asdict(manifest))
    (directory / "manifest.json").write_bytes(encoded)
    (directory / "manifest.sha256").write_text(digest(encoded), encoding="ascii")
    for path, content in files.items():
        target = safe_path(directory, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    result = ArchivedSnapshot(directory, manifest)
    audit_archive(result)
    return result


def _check_leaf(
    source: FrozenEvidence, manifest: ArchiveManifest, request: ResearchRequest
) -> None:
    if (
        source.provider != manifest.request.provider
        or source.dataset_id != manifest.request.dataset_id
        or source.dataset_revision != manifest.revision
        or source.window_start != request.window_start
        or source.window_end != request.window_end
        or source.version != 1
        or source.event_ids
        or source.clocks.ingested_at != manifest.acquired_end_at
        or any(
            getattr(source.clocks, n) is not None
            for n in (
                "effective_at",
                "historically_known_at",
                "published_at",
                "provider_available_at",
            )
        )
    ):
        _deny("SOURCE_BINDING_MISMATCH", "archive", "archive")


def audit_bound_archive(
    sources: tuple[FrozenEvidence, ...], request: ResearchRequest, raw: tuple
) -> dict:
    packs = [s for s in sources if s.role is EvidenceRole.ARCHIVE_MANIFEST]
    if len(packs) != 2:
        _deny("ARCHIVE_BINDING_MISSING", "manifest", "archive")
    unpacked = [(s, *_unpack(s, request)) for s in packs]
    by_layer = {manifest.layer: (source, manifest, files) for source, manifest, files in unpacked}
    if set(by_layer) != {"source", "normalized"}:
        _deny("ARCHIVE_BINDING_MISSING", "manifest", "archive")
    original_source, original, original_files = by_layer["source"]
    derived_source, derived, derived_files = by_layer["normalized"]
    licenses = [s for s in sources if s.role is EvidenceRole.LICENSE]
    if len(licenses) != 1:
        _deny("ARCHIVE_BINDING_MISSING", "archive", "archive")
    access = AccessDeclaration.from_dict(decode_json(licenses[0].content))
    _check_leaf(licenses[0], original, request)
    if access != original.request.access or licenses[0].source_ref != access.license_ref:
        _deny("SOURCE_BINDING_MISMATCH", "archive", "archive")
    if (
        licenses[0].source_id not in original_source.depends_on
        or original_source.source_id not in derived_source.depends_on
    ):
        _deny("SOURCE_DEPENDENCY_MISSING", "dependencies", "archive")
    matched = set()
    for manifest, files in ((original, original_files), (derived, derived_files)):
        for item in manifest.files:
            role = {
                "response": EvidenceRole.L0_RESPONSE,
                "receipt": EvidenceRole.ACQUISITION_RECEIPT,
                "normalized_raw": EvidenceRole.L1_PARTITION,
            }.get(item.role)
            if role is None or (manifest.layer == "normalized" and item.role != "normalized_raw"):
                continue
            candidates = [
                s
                for s in sources
                if s.role is role
                and s.dataset_revision == manifest.revision
                and s.partition_id == item.path
            ]
            if len(candidates) != 1:
                _deny("ARCHIVE_BINDING_MISSING", "files", "archive")
            leaf = candidates[0]
            _check_leaf(leaf, manifest, request)
            pack_source = original_source if manifest.layer == "source" else derived_source
            if (
                leaf.content != files[item.path]
                or leaf.source_ref != item.source_ref
                or pack_source.source_id not in leaf.depends_on
            ):
                _deny("SOURCE_BINDING_MISMATCH", "files", "archive")
            matched.add(leaf.source_id)
    leaves = {
        s.source_id
        for s in sources
        if s.role
        in {EvidenceRole.L0_RESPONSE, EvidenceRole.ACQUISITION_RECEIPT, EvidenceRole.L1_PARTITION}
    }
    if leaves != matched:
        _deny("SOURCE_BINDING_MISMATCH", "files", "archive")
    with tempfile.TemporaryDirectory(prefix="research-synthetic-audit-") as temporary:
        # Canonicalize only our newly created directory, including macOS /var aliases.
        # Incoming archive paths are never accepted; 5B still rejects archive symlinks.
        root = Path(temporary).resolve(strict=True)
        source_snapshot = _materialize(root, original, original_files)
        normalized_snapshot = _materialize(root, derived, derived_files)
        reproduced = normalize_archived_snapshot(source_snapshot, ArchiveStore(root / "derived"))
        if reproduced.manifest != normalized_snapshot.manifest or any(
            reproduced.read(f.path) != normalized_snapshot.read(f.path) for f in derived.files
        ):
            _deny("ARCHIVE_DERIVATION_MISMATCH", "archive", "archive")
        if _canonical(tuple(iter_raw_bars(normalized_snapshot))) != _canonical(
            tuple(
                sorted(
                    raw,
                    key=lambda b: (
                        b.bar_start_at,
                        b.security_id,
                        b.venue,
                        b.product_id,
                        b.bar_end_at,
                    ),
                )
            )
        ):
            _deny("ARCHIVE_RAW_BINDING", "raw", "archive")
    return {
        "status": "synthetic_archive_integrity_pass",
        "source_manifest_sha256": original.revision,
        "normalized_manifest_sha256": derived.revision,
        "raw_verified": False,
        "archive_ready": False,
    }
