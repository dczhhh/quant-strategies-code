"""Private, append-only 5F-1 artifacts; held hashes confer integrity, never trust."""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

from .archive import write_once
from .archive_contracts import ArchiveError, digest
from .demand import (
    DemandManifest,
    FileBinding,
    decode_demand_plan,
    deny,
    manifest_bytes,
)
from .entry_files import _path, read_bound_file
from .permission import PermissionPreparationError, _document
from .verification_contracts import VerificationContractError


def private_root(root: Path) -> Path:
    if not isinstance(root, Path):
        deny("PRIVATE_ROOT_INVALID", "root")
    try:
        _path(root, "root_probe")
        if any((parent / ".git").exists() for parent in (root, *root.parents)):
            deny("ROOT_INSIDE_REPOSITORY", "root")
        if os.name == "posix" and root.stat().st_mode & 0o077:
            deny("PRIVATE_ACL_REQUIRED", "root")
    except VerificationContractError as error:
        deny(error.code, "root")
    except OSError:
        deny("PRIVATE_ROOT_INVALID", "root")
    return root


def artifact_path(root: Path, relative: str) -> Path:
    try:
        path = _path(root, relative)
        for parent in path.parents:
            if parent == root:
                break
            if parent.exists() and os.name == "posix" and parent.stat().st_mode & 0o077:
                deny("PRIVATE_ACL_REQUIRED", "root")
        return path
    except VerificationContractError as error:
        deny(error.code, "path")


def private_write(root: Path, relative: str, content: bytes) -> None:
    try:
        path = artifact_path(root, relative)
        missing = []
        parent = path.parent
        while not parent.exists():
            missing.append(parent)
            parent = parent.parent
        for directory in reversed(missing):
            directory.mkdir(mode=0o700, exist_ok=True)
        path = artifact_path(root, relative)
        write_once(path, content)
        if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode) or path.stat().st_nlink != 1:
            deny("PATH_INVALID", "file")
    except ArchiveError as error:
        deny(error.code, "file")
    except OSError:
        deny("PRIVATE_WRITE_FAILED", "file")


def private_read(root: Path, relative: str, expected_sha256: str) -> bytes:
    try:
        artifact_path(root, relative)
        return read_bound_file(root, relative, expected_sha256)[0]
    except VerificationContractError as error:
        deny(error.code, "file")


def _checkpoint_identity(item: os.stat_result) -> tuple[int, ...]:
    # Reading may update atime (notably on APFS); it is not an integrity token.
    return (
        item.st_dev,
        item.st_ino,
        item.st_size,
        item.st_mtime_ns,
        item.st_mode,
        item.st_nlink,
        item.st_uid,
        item.st_gid,
        item.st_ctime_ns,
    )


def read_checkpoint(root: Path, relative: str) -> bytes:
    """Untrusted small metadata only; descriptor/path changes and aliases reject."""
    try:
        path = artifact_path(root, relative)
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or not 0 < before.st_size <= 32768
        ):
            deny("CHECKPOINT_INVALID", "session")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            # Windows may give stat/fstat different ctime semantics. Keep ctime
            # comparisons within each API, matching the reviewed file reader.
            if _checkpoint_identity(opened)[:-1] != _checkpoint_identity(before)[:-1]:
                deny("CHECKPOINT_CHANGED", "session")
            content = stream.read(32769)
            if _checkpoint_identity(os.fstat(stream.fileno())) != _checkpoint_identity(opened):
                deny("CHECKPOINT_CHANGED", "session")
        if (
            _checkpoint_identity(path.lstat()) != _checkpoint_identity(before)
            or artifact_path(root, relative) != path
            or len(content) != before.st_size
        ):
            deny("CHECKPOINT_CHANGED", "session")
        return content
    except OSError:
        deny("CHECKPOINT_INVALID", "session")


@dataclass(frozen=True, slots=True)
class DemandArchive:
    root: Path = field(repr=False)
    revision: str
    manifest: DemandManifest = field(repr=False)

    def read(self, binding: FileBinding) -> bytes:
        private_root(self.root)
        base = "revisions/" + self.revision + "/"
        encoded = private_read(self.root, base + "manifest.json", self.revision)
        if encoded != manifest_bytes(self.manifest):
            deny("LIVE_MANIFEST_CHANGED", "manifest")
        if self.manifest.mode == "licensed_private_unverified":
            from .demand_capture import _assert_archive_use

            _assert_archive_use(self.manifest, self.root)
        if binding not in self.manifest.bindings():
            deny("UNBOUND_ARTIFACT", "file")
        return private_read(self.root, base + binding.filename, binding.sha256)


def load_demand_archive(root: Path, expected_sha256: str, *, as_of: datetime) -> DemandArchive:
    private_root(root)
    if type(expected_sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        deny("INVALID_DIGEST", "manifest")
    base = "revisions/" + expected_sha256 + "/"
    encoded = private_read(root, base + "manifest.json", expected_sha256)
    try:
        manifest = cast(DemandManifest, _document(encoded, DemandManifest))
    except PermissionPreparationError as error:
        deny(error.code, "manifest")
    if manifest_bytes(manifest) != encoded:
        deny("NONCANONICAL_MANIFEST", "manifest")
    if type(as_of) is not datetime or as_of.utcoffset() != timedelta(0):
        deny("INVALID_TIME", "as_of")
    if (
        not manifest.acquired_start_at
        <= manifest.acquired_end_at
        <= as_of
        < manifest.retention_expires_at
    ):
        deny("ARCHIVE_TIME_OR_RETENTION_INVALID", "manifest")
    if (manifest.mode == "offline_fixture") != (manifest.execution_review_sha256 is None):
        deny("ARCHIVE_REVIEW_MODE_CONFLICT", "manifest")
    if manifest.mode == "licensed_private_unverified":
        from .demand_capture import _assert_archive_use

        _assert_archive_use(manifest, root)
    bindings = manifest.bindings()
    names = [item.filename for item in bindings]
    if (
        len(bindings) > 128
        or len(names) != len(set(names))
        or sum(b.size_bytes for b in bindings) > 16 * 1024 * 1024
        or any(not 0 < b.size_bytes <= 2 * 1024 * 1024 for b in bindings)
        or tuple(p.product for p in manifest.products) != ("raw", "comparison")
    ):
        deny("ARCHIVE_INVENTORY_INVALID", "manifest")
    exact = [
        (manifest.plan, "capture_plan", "plan"),
        (manifest.ledger, "budget_ledger", "ledger"),
        (manifest.diagnostics, "diagnostics", "diagnostics"),
    ]
    for product in manifest.products:
        if not 1 <= len(product.pages) <= 8 or not 0 <= product.row_count <= 390:
            deny("ARCHIVE_INVENTORY_INVALID", "manifest")
        exact.append((product.l1, product.product + "_l1", "l1"))
        for index, page in enumerate(product.pages):
            for role in ("request", "response", "receipt"):
                exact.append((getattr(page, role), f"{product.product}_p{index:03d}_{role}", role))
    if any(item.artifact_id != name or item.role != role for item, name, role in exact):
        deny("ARCHIVE_ROLE_CONFLICT", "manifest")
    directory = artifact_path(root, base + "manifest.json").parent
    if {p.name for p in directory.iterdir()} != set(names) | {"manifest.json"}:
        deny("ARCHIVE_INVENTORY_INVALID", "files")
    archive = DemandArchive(root, expected_sha256, manifest)
    plan = decode_demand_plan(archive.read(manifest.plan), as_of)
    if (
        manifest.prior_manifest_sha256 != plan.prior_manifest_sha256
        or manifest.retention_expires_at
        != manifest.acquired_end_at + timedelta(days=plan.retention_days)
    ):
        deny("ARCHIVE_SCOPE_CONFLICT", "manifest")
    for binding in bindings:
        if len(archive.read(binding)) != binding.size_bytes:
            deny("CONTENT_SIZE_MISMATCH", "file")
    return archive


def publish_demand_archive(
    root: Path, manifest: DemandManifest, content: dict[str, bytes], *, as_of: datetime
) -> DemandArchive:
    encoded = manifest_bytes(manifest)
    revision = digest(encoded)
    base = "revisions/" + revision + "/"
    bindings = manifest.bindings()
    if set(content) != {b.artifact_id for b in bindings} or any(
        digest(content[b.artifact_id]) != b.sha256 or len(content[b.artifact_id]) != b.size_bytes
        for b in bindings
    ):
        deny("PUBLICATION_BINDING_MISMATCH", "files")
    for binding in bindings:
        private_write(root, base + binding.filename, content[binding.artifact_id])
    # Last-write commit marker: incomplete revisions cannot be loaded or reused.
    private_write(root, base + "manifest.json", encoded)
    return load_demand_archive(root, revision, as_of=as_of)
