"""Bounded synthetic file declarations; hashes and paths never confer authority."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, cast

from .pit import _canonical
from .verification_contracts import (
    MAX_SOURCE_BYTES,
    MAX_SOURCES,
    EvidenceClocks,
    EvidenceRole,
    FrozenDatasetInput,
    FrozenEvidence,
    PipelineVersions,
    ResearchRequest,
    _deny,
    _hash,
    _reconstruct,
)
from .verification_wire import decode_json

FILES_SCHEMA = "research_input_files_v1"


def _keys(value: object, names: set[str]) -> dict:
    if type(value) is not dict or set(value) != names:
        _deny("INVALID_FILE_SCHEMA", "manifest", "files")
    return value


def _relative(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > 256
        or "\\" in value
        or ":" in value
        or any(ord(char) < 32 for char in value)
        or any(p in {"", ".", ".."} for p in value.split("/"))
        or PurePosixPath(value).is_absolute()
    ):
        _deny("PATH_INVALID", "path", "files")
    return value


def _identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def _path(root: Path, relative: str) -> Path:
    if not root.is_absolute() or ".." in root.parts:
        _deny("PATH_INVALID", "root", "files")
    target = root / _relative(relative)
    for path in (target, *target.parents):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            _deny("PATH_INVALID", "path", "files")
    if root.resolve(strict=True) != root or not root.is_dir():
        _deny("PATH_INVALID", "root", "files")
    return target


def read_bound_file(root: Path, relative: str, expected_sha256: str) -> tuple[bytes, tuple]:
    """Read once from a regular descriptor, checking path/descriptor identities twice.

    Consumers use these immutable bytes, never a later pathname read for execution.
    The extra re-reads detect replacements between build, final use and restart.
    """
    _hash(expected_sha256, "file_sha256")
    try:
        path = _path(root, relative)
        ancestry = (root, *path.parents[: len(path.parents) - len(root.parents)])
        directories = tuple((p, _identity(p.stat())) for p in dict.fromkeys(ancestry))
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            _deny("PATH_INVALID", "file", "files")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            # CPython on Windows may expose birth time in stat's ctime and change
            # time in fstat's ctime (python/cpython#157671). Compare dev/ino/size/
            # mtime across APIs; keep full ctime tokens for stat->stat and fd->fd.
            if _identity(opened)[:4] != _identity(before)[:4] or not stat.S_ISREG(opened.st_mode):
                _deny("FILE_CHANGED", "file", "files")
            content = stream.read(MAX_SOURCE_BYTES + 1)
            if _identity(os.fstat(stream.fileno())) != _identity(opened):
                _deny("FILE_CHANGED", "file", "files")
        if (
            _path(root, relative) != path
            or _identity(path.lstat()) != _identity(before)
            or any(_identity(p.stat()) != identity for p, identity in directories)
        ):
            _deny("FILE_CHANGED", "file", "files")
        if not 0 < len(content) <= MAX_SOURCE_BYTES:
            _deny("SOURCE_BOUNDS", "file", "files")
        if hashlib.sha256(content).hexdigest() != expected_sha256:
            _deny("CONTENT_HASH_MISMATCH", "file", "files")
        return content, _identity(before)
    except OSError:
        _deny("FILE_UNREADABLE", "file", "files")


def encode_file_manifest(snapshot: FrozenDatasetInput, paths: dict[str, str]) -> bytes:
    """Encode declarations only; the caller freezes the returned manifest digest separately."""
    snapshot = _reconstruct(snapshot, FrozenDatasetInput, "snapshot")
    if type(paths) is not dict or set(paths) != {s.source_id for s in snapshot.sources}:
        _deny("INVALID_FILE_SCHEMA", "paths", "files")
    sources = []
    for source in snapshot.sources:
        item = asdict(source)
        item.pop("content")
        item.update(path=_relative(paths[source.source_id]), size_bytes=len(source.content))
        sources.append(item)
    if len({item["path"] for item in sources}) != len(sources):
        _deny("PATH_ALIAS", "paths", "files")
    return _canonical(
        {
            "schema_version": FILES_SCHEMA,
            "request": snapshot.request,
            "versions": snapshot.versions,
            "sources": sources,
            "input_root_sha256": snapshot.expected_root_sha256,
        }
    )


@dataclass(frozen=True, slots=True)
class FrozenDatasetFiles:
    """An integrity source, never a trusted bundle or authority credential."""

    root: Path
    manifest_path: str
    manifest_sha256: str
    snapshot: FrozenDatasetInput
    identities: tuple

    def reload(self, reviewed_root_sha256: str) -> FrozenDatasetInput:
        other = load_dataset_files(
            self.root,
            self.manifest_path,
            reviewed_manifest_sha256=self.manifest_sha256,
            reviewed_root_sha256=reviewed_root_sha256,
        )
        if self.identities != other.identities:
            _deny("FILE_CHANGED", "file", "files")
        self.snapshot.assert_bound(reviewed_root_sha256)
        return other.snapshot


def load_dataset_files(
    root: Path,
    manifest_path: str,
    *,
    reviewed_manifest_sha256: str,
    reviewed_root_sha256: str,
) -> FrozenDatasetFiles:
    """Load exact bytes under a caller-declared root; real admission remains deferred.

    The manifest cannot supply either independently held digest. Source references
    stay opaque: only the explicit relative path fields are opened.
    """
    if not isinstance(root, Path):
        _deny("INVALID_TYPE", "root", "files")
    _hash(reviewed_root_sha256, "reviewed_root_sha256")
    content, identity = read_bound_file(root, manifest_path, reviewed_manifest_sha256)
    payload = _keys(
        decode_json(content),
        {"schema_version", "request", "versions", "sources", "input_root_sha256"},
    )
    if payload["schema_version"] != FILES_SCHEMA:
        _deny("INVALID_FILE_SCHEMA", "manifest", "files")
    if payload["input_root_sha256"] != reviewed_root_sha256:
        _deny("SOURCE_ROOT_MISMATCH", "reviewed_root_sha256", "binding")
    request = ResearchRequest.from_dict(payload["request"])
    versions = PipelineVersions(
        **_keys(payload["versions"], {f.name for f in fields(PipelineVersions)})
    )
    if type(payload["sources"]) is not list or not 0 < len(payload["sources"]) <= MAX_SOURCES:
        _deny("SOURCE_BOUNDS", "sources", "files")
    sources, identities, paths = [], [(manifest_path, identity)], {manifest_path}
    names = {f.name for f in fields(FrozenEvidence)} - {"content"}
    try:
        for item in payload["sources"]:
            value = dict(_keys(item, names | {"path", "size_bytes"}))
            relative = _relative(value.pop("path"))
            if relative in paths:
                _deny("PATH_ALIAS", "paths", "files")
            paths.add(relative)
            body, identity = read_bound_file(root, relative, value["sha256"])
            size = value.pop("size_bytes")
            if type(size) is not int or size != len(body):
                _deny("CONTENT_SIZE_MISMATCH", "file", "files")
            clocks = _keys(value["clocks"], {f.name for f in fields(EvidenceClocks)})
            value["clocks"] = EvidenceClocks(
                **cast(
                    Any,
                    {
                        k: datetime.fromisoformat(v) if v is not None else None
                        for k, v in clocks.items()
                    },
                )
            )
            value["role"] = EvidenceRole(value["role"])
            for name in ("window_start", "window_end"):
                value[name] = datetime.fromisoformat(value[name])
            for name in ("event_ids", "depends_on"):
                if type(value[name]) is not list:
                    _deny("INVALID_FILE_SCHEMA", "manifest", "files")
                value[name] = tuple(value[name])
            sources.append(FrozenEvidence(**value, content=body))
            identities.append((relative, identity))
    except (TypeError, ValueError) as error:
        from .verification_contracts import VerificationContractError

        if isinstance(error, VerificationContractError):
            raise
        _deny("INVALID_FILE_SCHEMA", "manifest", "files")
    snapshot = FrozenDatasetInput(
        request=request,
        versions=versions,
        sources=tuple(sources),
        expected_root_sha256=reviewed_root_sha256,
    )
    # Freeze the original root directory inode too; renaming the whole tree is a replacement.
    identities.append(("/root", _identity(root.stat())))
    return FrozenDatasetFiles(
        root, manifest_path, reviewed_manifest_sha256, snapshot, tuple(identities)
    )
