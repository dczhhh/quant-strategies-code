"""Independent manifest/root bindings, descriptor races, restart and CLI admission."""

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_research_entry import Observe, bound, no_engine, rejects, settings
from test_verification_orchestration import fixture, freeze

from quant_constraints.research.data import entry, entry_cli, entry_files
from quant_constraints.research.data.entry import (
    build_synthetic_test_engine,
    restore_synthetic_test_engine,
)
from quant_constraints.research.data.entry_files import (
    encode_file_manifest,
    load_dataset_files,
    read_bound_file,
)
from quant_constraints.research.data.pit import _canonical
from quant_constraints.research.data.verification_contracts import EvidenceRole


def sha(content):
    return hashlib.sha256(content).hexdigest()


def files_for(tmp_path, snapshot=None):
    snapshot = snapshot or bound()[0]
    root = tmp_path.resolve()
    root.mkdir(parents=True, exist_ok=True)
    paths = {s.source_id: "sources/" + s.source_id + ".json" for s in snapshot.sources}
    for source in snapshot.sources:
        path = root / paths[source.source_id]
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(source.content)
    manifest = encode_file_manifest(snapshot, paths)
    (root / "manifest.json").write_bytes(manifest)
    return root, snapshot, paths, manifest


def load(root, snapshot, manifest):
    return load_dataset_files(
        root,
        "manifest.json",
        reviewed_manifest_sha256=sha(manifest),
        reviewed_root_sha256=snapshot.expected_root_sha256,
    )


def arguments(root, snapshot, manifest, mode="synthetic_test"):
    return [
        "--mode",
        mode,
        "--root",
        str(root),
        "--manifest",
        "manifest.json",
        "--manifest-sha256",
        sha(manifest),
        "--input-root-sha256",
        snapshot.expected_root_sha256,
    ]


def test_files_execute_copied_bytes_and_repeat_restart_provenance(tmp_path):
    snapshot, config = bound()
    root, snapshot, paths, manifest = files_for(tmp_path, snapshot)
    files = load(root, snapshot, manifest)
    assert files.snapshot == snapshot and files.reload(snapshot.expected_root_sha256) == snapshot
    plan = build_synthetic_test_engine(
        files, config, Observe, reviewed_root_sha256=snapshot.expected_root_sha256
    )
    checkpoint = plan.checkpoint()
    restored = restore_synthetic_test_engine(
        checkpoint, files, config, Observe, reviewed_root_sha256=snapshot.expected_root_sha256
    )
    first, second = plan.run(), restored.run()
    assert first.metrics["data_provenance_v1"] == second.metrics["data_provenance_v1"]
    assert str(root) not in json.dumps(first.metrics["data_provenance_v1"])


@pytest.mark.parametrize(
    "role",
    [
        r
        for r in EvidenceRole
        if r
        not in {
            EvidenceRole.ARCHIVE_MANIFEST,
            EvidenceRole.L0_RESPONSE,
            EvidenceRole.ACQUISITION_RECEIPT,
            EvidenceRole.L1_PARTITION,
            EvidenceRole.LICENSE,
        }
    ],
)
def test_every_domain_role_file_change_fails_bound_manifest(tmp_path, role):
    req, records = fixture(macro=True, tax=True)
    snapshot = freeze(req, records)
    root, snapshot, paths, manifest = files_for(tmp_path, snapshot)
    (root / paths[role.value]).write_bytes(b"changed-private-source")
    rejects("CONTENT_HASH_MISMATCH", lambda: load(root, snapshot, manifest))


def test_new_manifest_and_rehashed_role_cannot_replace_independent_roots(tmp_path):
    root, snapshot, paths, manifest = files_for(tmp_path)
    payload = json.loads(manifest)
    payload["sources"][0]["sha256"] = sha(b"changed")
    new_manifest = _canonical(payload)
    (root / "manifest.json").write_bytes(new_manifest)
    rejects("CONTENT_HASH_MISMATCH", lambda: load(root, snapshot, manifest))
    payload["input_root_sha256"] = "e" * 64
    new_manifest = _canonical(payload)
    (root / "manifest.json").write_bytes(new_manifest)
    rejects("SOURCE_ROOT_MISMATCH", lambda: load(root, snapshot, new_manifest))


@pytest.mark.parametrize("phase", ["run", "restore"])
def test_file_replaced_after_build_is_rejected_before_any_engine(tmp_path, monkeypatch, phase):
    snapshot, config = bound()
    root, snapshot, paths, manifest = files_for(tmp_path, snapshot)
    files = load(root, snapshot, manifest)
    plan = build_synthetic_test_engine(
        files, config, Observe, reviewed_root_sha256=snapshot.expected_root_sha256
    )
    checkpoint = plan.checkpoint()
    path = root / paths["raw"]
    replacement = root / "replacement"
    replacement.write_bytes(path.read_bytes())
    os.replace(replacement, path)
    no_engine(monkeypatch)
    if phase == "run":
        rejects("FILE_CHANGED", plan.run)
    else:
        rejects(
            "FILE_CHANGED",
            lambda: restore_synthetic_test_engine(
                checkpoint,
                files,
                config,
                Observe,
                reviewed_root_sha256=snapshot.expected_root_sha256,
            ),
        )


def test_whole_root_replacement_is_detected_even_when_all_bytes_are_same(tmp_path, monkeypatch):
    root, snapshot, paths, manifest = files_for(tmp_path / "root")
    files = load(root, snapshot, manifest)
    root.rename(tmp_path / "old-root")
    files_for(root, snapshot)
    rejects("FILE_CHANGED", lambda: files.reload(snapshot.expected_root_sha256))


def test_mutation_by_strategy_constructor_is_checked_before_broker(tmp_path, monkeypatch):
    target = None

    class ChangeSource(Observe):
        def __init__(self):
            target.write_bytes(b"changed-by-constructor")

    snapshot, config = bound(strategy_type=ChangeSource)
    root, snapshot, paths, manifest = files_for(tmp_path, snapshot)
    target = root / paths["raw"]
    plan = build_synthetic_test_engine(
        load(root, snapshot, manifest),
        config,
        ChangeSource,
        reviewed_root_sha256=snapshot.expected_root_sha256,
    )
    no_engine(monkeypatch)
    rejects("CONTENT_HASH_MISMATCH", plan.run)


def test_mutation_during_engine_construction_is_checked_before_on_start(tmp_path, monkeypatch):
    root, snapshot, paths, manifest = files_for(tmp_path)
    config = settings()
    plan = build_synthetic_test_engine(
        load(root, snapshot, manifest),
        config,
        Observe,
        reviewed_root_sha256=snapshot.expected_root_sha256,
    )
    original, engines = entry.constrained_engine, []

    def change(*args, **kwargs):
        engine = original(*args, **kwargs)
        engines.append(engine)
        (root / paths["raw"]).write_bytes(b"changed-after-construction")
        return engine

    monkeypatch.setattr(entry, "constrained_engine", change)
    rejects("CONTENT_HASH_MISMATCH", plan.run)
    assert engines[0].broker.cash == 10000
    assert not engines[0]._has_run and not engines[0].broker.positions
    assert not engines[0].broker.pending_orders


@pytest.mark.parametrize(
    "relative",
    [
        "",
        ".",
        "..",
        "../escape",
        "/absolute",
        "a/../b",
        "a//b",
        "a/./b",
        "C:drive",
        "a\\b",
        "a\x00b",
        "a\nb",
        "x" * 257,
    ],
)
def test_paths_cannot_escape_or_alias_approved_root(tmp_path, relative):
    rejects("PATH_INVALID", lambda: read_bound_file(tmp_path.resolve(), relative, "a" * 64))


def test_root_must_be_absolute_existing_directory_and_not_symlink(tmp_path):
    rejects("PATH_INVALID", lambda: read_bound_file(Path("relative"), "input", "a" * 64))
    root = tmp_path / "root"
    root.mkdir()
    rejects("PATH_INVALID", lambda: read_bound_file(root / ".." / "root", "input", "a" * 64))
    file = tmp_path / "file"
    file.write_bytes(b"x")
    rejects("PATH_INVALID", lambda: read_bound_file(file, "input", "a" * 64))
    rejects("FILE_UNREADABLE", lambda: read_bound_file(tmp_path / "missing", "input", "a" * 64))
    rejects(
        "INVALID_TYPE",
        lambda: load_dataset_files(
            str(root), "input", reviewed_manifest_sha256="a" * 64, reviewed_root_sha256="a" * 64
        ),
    )


@pytest.mark.parametrize("location", ["root", "parent", "file"])
def test_actual_symlinks_are_rejected(tmp_path, location):
    root = tmp_path.resolve() / "root"
    root.mkdir()
    (root / "data").mkdir()
    (root / "data" / "input").write_bytes(b"x")
    relative = "data/input"
    link = tmp_path / "link"
    try:
        if location == "root":
            link.symlink_to(root, target_is_directory=True)
            root = link
        elif location == "parent":
            link.symlink_to(root / "data", target_is_directory=True)
            (root / "alias").symlink_to(link, target_is_directory=True)
            relative = "alias/input"
        else:
            (root / "alias").symlink_to(root / relative)
            relative = "alias"
    except OSError:
        pytest.skip("Host does not permit creation of test symlinks")
    rejects("PATH_INVALID", lambda: read_bound_file(root, relative, sha(b"x")))


def test_hardlink_alias_and_non_regular_file_fail_closed(tmp_path):
    (tmp_path / "data").write_bytes(b"x")
    os.link(tmp_path / "data", tmp_path / "alias")
    rejects("PATH_INVALID", lambda: read_bound_file(tmp_path.resolve(), "data", sha(b"x")))
    (tmp_path / "directory").mkdir()
    rejects("PATH_INVALID", lambda: read_bound_file(tmp_path.resolve(), "directory", sha(b"x")))


def test_open_time_replacement_detected_by_descriptor_identity(tmp_path, monkeypatch):
    path = tmp_path / "file"
    path.write_bytes(b"x")
    original = entry_files.os.open

    def replace_before_open(target, flags):
        replacement = tmp_path / "new"
        replacement.write_bytes(b"x")
        os.replace(replacement, target)
        return original(target, flags)

    monkeypatch.setattr(entry_files.os, "open", replace_before_open)
    rejects("FILE_CHANGED", lambda: read_bound_file(tmp_path.resolve(), "file", sha(b"x")))


def test_in_place_mutation_during_read_detected(tmp_path, monkeypatch):
    (tmp_path / "file").write_bytes(b"x")
    actual = entry_files.os.fstat
    calls = 0

    def changed_stat(fd):
        nonlocal calls
        value = actual(fd)
        calls += 1
        return SimpleNamespace(
            st_dev=value.st_dev,
            st_ino=value.st_ino,
            st_size=value.st_size,
            st_mtime_ns=value.st_mtime_ns,
            st_ctime_ns=value.st_ctime_ns + int(calls > 1),
            st_mode=value.st_mode,
        )

    monkeypatch.setattr(entry_files.os, "fstat", changed_stat)
    rejects("FILE_CHANGED", lambda: read_bound_file(tmp_path.resolve(), "file", sha(b"x")))


def test_windows_stat_fstat_ctime_semantics_do_not_create_false_replacement(tmp_path, monkeypatch):
    (tmp_path / "file").write_bytes(b"x")
    actual = entry_files.os.fstat

    def different_ctime(fd):
        value = actual(fd)
        return SimpleNamespace(
            st_dev=value.st_dev,
            st_ino=value.st_ino,
            st_size=value.st_size,
            st_mtime_ns=value.st_mtime_ns,
            st_ctime_ns=value.st_ctime_ns + 100000,
            st_mode=value.st_mode,
        )

    monkeypatch.setattr(entry_files.os, "fstat", different_ctime)
    assert read_bound_file(tmp_path.resolve(), "file", sha(b"x"))[0] == b"x"


def test_post_read_replacement_and_bounds_detected(tmp_path, monkeypatch):
    (tmp_path / "file").write_bytes(b"x")
    original = entry_files._path
    calls = 0

    def after_read(root, relative):
        nonlocal calls
        result = original(root, relative)
        calls += 1
        if calls == 2:
            replacement = tmp_path / "replacement"
            replacement.write_bytes(b"x")
            os.replace(replacement, result)
        return result

    monkeypatch.setattr(entry_files, "_path", after_read)
    rejects("FILE_CHANGED", lambda: read_bound_file(tmp_path.resolve(), "file", sha(b"x")))
    monkeypatch.setattr(entry_files, "_path", original)
    (tmp_path / "file").write_bytes(b"")
    rejects("SOURCE_BOUNDS", lambda: read_bound_file(tmp_path.resolve(), "file", sha(b"")))
    (tmp_path / "file").write_bytes(b"long")
    monkeypatch.setattr(entry_files, "MAX_SOURCE_BYTES", 2)
    rejects("SOURCE_BOUNDS", lambda: read_bound_file(tmp_path.resolve(), "file", sha(b"long")))


@pytest.mark.parametrize(
    "change,code",
    [
        (lambda p: p.update(schema_version="old"), "INVALID_FILE_SCHEMA"),
        (lambda p: p.update(verified=True), "INVALID_FILE_SCHEMA"),
        (lambda p: p.update(sources=[]), "SOURCE_BOUNDS"),
        (lambda p: p.update(sources={}), "SOURCE_BOUNDS"),
        (lambda p: p["versions"].pop("adapter"), "INVALID_FILE_SCHEMA"),
        (lambda p: p["sources"][0].update(path="manifest.json"), "PATH_ALIAS"),
        (lambda p: p["sources"][0].update(size_bytes=True), "CONTENT_SIZE_MISMATCH"),
        (lambda p: p["sources"][0].update(size_bytes=9999), "CONTENT_SIZE_MISMATCH"),
        (lambda p: p["sources"][0].update(role="fake"), "INVALID_FILE_SCHEMA"),
        (lambda p: p["sources"][0].update(window_start="broken"), "INVALID_FILE_SCHEMA"),
        (lambda p: p["sources"][0].update(depends_on={}), "INVALID_FILE_SCHEMA"),
        (lambda p: p["sources"][0]["clocks"].update(published_at="broken"), "INVALID_FILE_SCHEMA"),
    ],
)
def test_manifest_is_strict_and_never_a_certificate(tmp_path, change, code):
    root, snapshot, paths, manifest = files_for(tmp_path)
    payload = json.loads(manifest)
    change(payload)
    manifest = _canonical(payload)
    (root / "manifest.json").write_bytes(manifest)
    rejects(code, lambda: load(root, snapshot, manifest))


def test_manifest_encoder_requires_all_unique_relative_paths():
    snapshot, _ = bound()
    rejects("INVALID_FILE_SCHEMA", lambda: encode_file_manifest(snapshot, {}))
    paths = {s.source_id: "same.json" for s in snapshot.sources}
    rejects("PATH_ALIAS", lambda: encode_file_manifest(snapshot, paths))


def cli_files(tmp_path):
    config = settings()
    snapshot, _ = bound(config=config, strategy_type=entry_cli._NoOrders)
    root, snapshot, paths, manifest = files_for(tmp_path, snapshot)
    from dataclasses import asdict

    content = _canonical(
        {
            "schema_version": entry_cli.CONFIG_SCHEMA,
            "backtest": config.backtest.to_dict(),
            "constraints": asdict(config.constraints),
        }
    )
    (root / "config.json").write_bytes(content)
    return root, snapshot, manifest, content


def test_cli_runs_only_explicit_isolated_no_order_probe_and_is_redacted(tmp_path, capsys):
    root, snapshot, manifest, config = cli_files(tmp_path)
    args = arguments(root, snapshot, manifest) + [
        "--config",
        "config.json",
        "--config-sha256",
        sha(config),
    ]
    assert entry_cli.main(args) == 0
    output = capsys.readouterr()
    audit = json.loads(output.out)
    assert audit["status"] == "offline_entry_contract_pass" and not audit["trusted_entry"]
    assert str(root) not in output.out and not output.err


@pytest.mark.parametrize("mode", ["verified_research", "synthetic_test"])
def test_cli_cannot_fallback_to_legacy_or_accept_missing_config(
    tmp_path, monkeypatch, capsys, mode
):
    root, snapshot, manifest, config = cli_files(tmp_path)
    no_engine(monkeypatch)
    assert entry_cli.main(arguments(root, snapshot, manifest, mode)) == 2
    audit = json.loads(capsys.readouterr().out)
    assert audit["code"] == (
        "SYNTHETIC_NOT_TRUSTED" if mode == "verified_research" else "MISSING_CONFIG"
    )


@pytest.mark.parametrize(
    "config",
    [
        b'{"schema_version":"old","backtest":{},"constraints":{}}',
        b'{"schema_version":"synthetic_research_config_v1","backtest":{},"constraints":{"private-secret":"bad"}}',
    ],
)
def test_cli_invalid_config_diagnostic_never_echoes_details(tmp_path, monkeypatch, capsys, config):
    root, snapshot, manifest, _ = cli_files(tmp_path)
    (root / "config.json").write_bytes(config)
    no_engine(monkeypatch)
    assert (
        entry_cli.main(
            arguments(root, snapshot, manifest)
            + ["--config", "config.json", "--config-sha256", sha(config)]
        )
        == 2
    )
    output = capsys.readouterr()
    assert json.loads(output.out)["status"] == "reject"
    assert "private-secret" not in output.out and not output.err


def test_cli_missing_or_unknown_mode_and_sensitive_arguments_are_redacted(capsys):
    assert entry_cli.main(["--private-secret=token"]) == 2
    output = capsys.readouterr()
    assert json.loads(output.out)["code"] == "INVALID_CLI_ARGUMENTS"
    assert "token" not in output.out and not output.err


def test_cli_unissued_real_is_pending_and_cannot_construct_engine(tmp_path, monkeypatch, capsys):
    from test_verification_orchestration import rebind

    snapshot, _ = bound()
    request = replace(snapshot.request, input_kind="real")
    license_source = replace(
        snapshot.sources[0], role=EvidenceRole.LICENSE, source_id="license", depends_on=()
    )
    declared_real = rebind(request, snapshot.sources + (license_source,))
    root, snapshot, _, manifest = files_for(tmp_path, declared_real)
    no_engine(monkeypatch)
    assert entry_cli.main(arguments(root, snapshot, manifest, "verified_research")) == 2
    audit = json.loads(capsys.readouterr().out)
    assert audit["status"] == "real_evidence_pending"
    assert audit["code"] == "REAL_EVIDENCE_DEFERRED"
    assert not audit["raw_verified"] and not audit["trusted_entry"]


def test_cli_preserves_pit_reject_code_and_redacts_fields(tmp_path, monkeypatch, capsys):
    from quant_constraints.research.data.pit import PITError

    root, snapshot, manifest, config = cli_files(tmp_path)
    monkeypatch.setattr(
        entry_cli,
        "build_synthetic_test_engine",
        lambda *_a, **_k: (_ for _ in ()).throw(PITError("PIT_COVERAGE_UNKNOWN", "private-query")),
    )
    assert (
        entry_cli.main(
            arguments(root, snapshot, manifest)
            + ["--config", "config.json", "--config-sha256", sha(config)]
        )
        == 2
    )
    audit = json.loads(capsys.readouterr().out)
    assert audit["code"] == "PIT_COVERAGE_UNKNOWN" and audit["field"] == "input"
