"""Invented bytes only; create-only preservation and no provider credentials."""

from copy import deepcopy

import pytest

from tests.test_private_archive_migration import FakeGitHub
from tools import rotation_four_year_input_archive as a


def test_fresh_verify_rebuild_compares_all_bytes_and_never_writes():
    archive = FakeGitHub(a.c.m.DESTINATION)
    files = a.pack({"inputs.json": b"invented inputs", "audit.json": b"invented audit"}, "e" * 40)
    with pytest.raises(ValueError, match="ARCHIVE_MISSING"):
        a.retain(archive, files, verify_only=True)
    digest = a.retain(archive, files, verify_only=False)
    refs = deepcopy(archive.refs)
    archive.events.clear()
    assert a.retain(archive, files, verify_only=True) == digest
    assert all(method == "GET" for method, _ in archive.events)
    assert archive.refs == refs
    changed = a.pack(
        {"inputs.json": b"invented changed inputs", "audit.json": b"invented audit"}, "e" * 40
    )
    with pytest.raises(ValueError, match="FRESH_INPUT_BYTES"):
        a.retain(archive, changed, verify_only=False)


def test_unexpected_path_or_source_cannot_replace_an_accepted_archive():
    archive = FakeGitHub(a.c.m.DESTINATION)
    files = a.pack({"inputs.json": b"invented inputs", "audit.json": b"invented audit"}, "e" * 40)
    a.retain(archive, files, verify_only=False)
    changed = a.pack({"inputs.json": b"invented inputs", "audit.json": b"invented audit"}, "f" * 40)
    with pytest.raises(ValueError, match="FRESH_INPUT_BYTES"):
        a.retain(archive, changed, verify_only=False)
    files["unexpected.json"] = b"{}"
    with pytest.raises(ValueError, match="ARCHIVE_SCOPE"):
        a.retain(archive, files, verify_only=False)


def test_logical_scope_and_expansion_limits_apply_before_writes(monkeypatch):
    with pytest.raises(ValueError, match="LOGICAL_SET"):
        a.pack({"inputs.json": b"invented"}, "e" * 40)
    monkeypatch.setitem(a.LIMITS, "inputs.json", 3)
    with pytest.raises(ValueError, match="LOGICAL_EXPANSION"):
        a.pack({"inputs.json": b"oversize", "audit.json": b"invented"}, "e" * 40)


def test_factory_shares_immutable_objects_but_keeps_default_clients_read_only():
    factory = a.Factory("INVENTED")
    first, second, writer = factory(), factory(), factory(writable=True)
    assert first.objects is second.objects is writer.objects
    assert first.bodies is second.bodies is writer.bodies
    assert first.destination.writable is second.destination.writable is False
    assert writer.destination.writable is True


def test_cli_failure_never_prints_exception_body(monkeypatch, capsys):
    monkeypatch.setattr(a.sys, "argv", ["archive", "run", "e" * 40])
    monkeypatch.setattr(a, "guarded", lambda _source: None)
    monkeypatch.setattr(
        a.a, "build", lambda *_args: (_ for _ in ()).throw(ValueError("PRIVATE_BODY"))
    )
    assert a.main() == 1
    assert "PRIVATE_BODY" not in capsys.readouterr().out
