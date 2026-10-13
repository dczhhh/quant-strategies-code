"""Validate distribution contents and reproducibility."""

from __future__ import annotations

import argparse
import hashlib
import tarfile
import zipfile
from pathlib import Path

_ROOT = Path(__file__).parents[1]
_PACKAGE = _ROOT / "src" / "ml4t" / "backtest"
_EXTENSION = _ROOT / "src" / "quant_constraints"
_TESTS = _ROOT / "tests"
_INTERNAL_NAMES = {"AGENTS.md", "CLAUDE.md"}
_APPROVED_TEST_DATA = (
    _TESTS / "compatibility" / "snapshots",
    _TESTS / "fixtures" / "artifacts",
)
_APPROVED_PACKAGE_DATA = {
    "src/quant_constraints/research/data/resources/continuous_batch_v1.json": "2d28389406da6d46ff798ea46ff91927d54ea0607d62b9ebc8b804e7312f0e1a",
    "src/quant_constraints/research/data/resources/sample_batch_v1.json": "387612d9dceb0579e62c6220c64d2f62b6845d0bbe963fc216ee3b821b8f638f",
}


def _python_files(root: Path) -> set[str]:
    return {
        path.relative_to(_ROOT).as_posix()
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    }


def _data_files(root: Path) -> set[str]:
    if not root.is_dir():
        return set()
    return {
        path.relative_to(_ROOT).as_posix()
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }


def _single(directory: Path, pattern: str) -> Path:
    matches = list(directory.glob(pattern))
    if len(matches) != 1:
        raise ValueError(f"Expected one {pattern} in {directory}, found {len(matches)}")
    return matches[0]


def _wheel_manifest(wheel: Path) -> tuple[set[str], str]:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    metadata = [name for name in names if name.endswith(".dist-info/METADATA")]
    if len(metadata) != 1:
        raise ValueError(f"Expected one wheel METADATA file, found {metadata}")
    dist_info = metadata[0].removesuffix("/METADATA")
    return names, dist_info


def _sdist_manifest(sdist: Path) -> set[str]:
    with tarfile.open(sdist) as archive:
        names = [member.name for member in archive.getmembers() if member.isfile()]
    roots = {name.split("/", 1)[0] for name in names}
    if len(roots) != 1:
        raise ValueError(f"Source distribution has unexpected roots: {sorted(roots)}")
    root = roots.pop()
    return {name.removeprefix(f"{root}/") for name in names}


def _manifest_diff(actual: set[str], expected: set[str], label: str) -> list[str]:
    failures: list[str] = []
    unexpected = sorted(actual - expected)
    missing = sorted(expected - actual)
    if unexpected:
        failures.append(f"{label} has unexpected files: {unexpected}")
    if missing:
        failures.append(f"{label} is missing files: {missing}")
    return failures


def _resource_failures(wheel: Path, sdist: Path) -> list[str]:
    """Only literal approved resources, with bounded byte reads and exact hashes."""
    if not _APPROVED_PACKAGE_DATA:
        return []
    failures = []
    with zipfile.ZipFile(wheel) as zipped, tarfile.open(sdist) as source:
        for path, expected in _APPROVED_PACKAGE_DATA.items():
            name = path.removeprefix("src/")
            if name not in zipped.namelist():
                failures.append(f"wheel is missing approved resource: {name}")
            else:
                with zipped.open(name) as stream:
                    content = stream.read(65537)
                if hashlib.sha256(content).hexdigest() != expected:
                    failures.append(f"wheel approved resource bytes changed: {name}")
            members = [
                m for m in source.getmembers() if m.isfile() and m.name.split("/", 1)[-1] == path
            ]
            if len(members) != 1:
                failures.append(f"sdist is missing approved resource: {path}")
            else:
                stream = source.extractfile(members[0])
                if stream is None:
                    failures.append(f"sdist approved resource is unreadable: {path}")
                else:
                    with stream:
                        content = stream.read(65537)
                    if hashlib.sha256(content).hexdigest() != expected:
                        failures.append(f"sdist approved resource bytes changed: {path}")
    return failures


def artifact_failures(directory: Path) -> list[str]:
    wheel = _single(directory, "*.whl")
    sdist = _single(directory, "*.tar.gz")
    wheel_files, dist_info = _wheel_manifest(wheel)
    sdist_files = _sdist_manifest(sdist)

    source_files = _python_files(_PACKAGE) | _python_files(_EXTENSION)
    package_files = {path.removeprefix("src/") for path in source_files} | {
        "ml4t/backtest/py.typed"
    }
    package_data = {
        path.relative_to(_ROOT).as_posix() for path in (_PACKAGE / "example_data").glob("*.csv")
    }
    package_data |= set(_APPROVED_PACKAGE_DATA)
    expected_wheel = (
        package_files
        | {path.removeprefix("src/") for path in package_data}
        | {
            f"{dist_info}/METADATA",
            f"{dist_info}/WHEEL",
            f"{dist_info}/licenses/LICENSE",
            f"{dist_info}/RECORD",
        }
    )
    expected_sdist = (
        source_files
        | package_data
        | _python_files(_TESTS)
        | set().union(*(_data_files(path) for path in _APPROVED_TEST_DATA))
        | {
            ".gitignore",
            "CHANGELOG.md",
            "LICENSE",
            "README.md",
            "PKG-INFO",
            "pyproject.toml",
            "src/ml4t/backtest/py.typed",
        }
    )

    failures = _manifest_diff(wheel_files, expected_wheel, "wheel")
    failures.extend(_manifest_diff(sdist_files, expected_sdist, "sdist"))
    failures.extend(_resource_failures(wheel, sdist))
    for name in wheel_files | sdist_files:
        path = Path(name)
        if _INTERNAL_NAMES.intersection(path.parts) or any(
            part in {".claude", ".workspace"} for part in path.parts
        ):
            failures.append(f"Distribution contains internal agent material: {name}")
    return failures


def reproducibility_failures(first: Path, second: Path) -> list[str]:
    failures: list[str] = []
    for pattern in ("*.whl", "*.tar.gz"):
        left = _single(first, pattern)
        right = _single(second, pattern)
        if left.name != right.name:
            failures.append(f"Artifact names differ: {left.name} != {right.name}")
            continue
        left_digest = hashlib.sha256(left.read_bytes()).hexdigest()
        right_digest = hashlib.sha256(right.read_bytes()).hexdigest()
        if left_digest != right_digest:
            failures.append(f"Artifact is not reproducible: {left.name}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact_directory", type=Path)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    failures = artifact_failures(args.artifact_directory)
    if args.compare is not None:
        failures.extend(artifact_failures(args.compare))
        failures.extend(reproducibility_failures(args.artifact_directory, args.compare))
    if failures:
        for failure in failures:
            print(failure)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
