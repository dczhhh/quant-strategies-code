"""Coverage and issuer negative controls using invented documents only."""

import copy
from datetime import date
from decimal import Decimal

import pytest

from tests.test_private_archive_migration import FakeGitHub
from tests.test_real_etf_2025 import documents
from tools import rotation_four_year_gap_audit as a


def sheets(changes=None):
    docs = documents(changes=changes)
    p = a.c.p
    return tuple(p.workbook(docs[p.DOCUMENTS[k]["id"]]["body"]) for k in range(2))


def event():
    return {
        "ex": date(2025, 9, 19),
        "record": date(2025, 9, 19),
        "pay": date(2025, 10, 31),
        "amount": Decimal("1"),
    }


def test_exact_native_cash_and_same_year_ordinary_tax_components_pass():
    history, tax = sheets()
    assert a.ordinary_2025(history, tax, "SPY", event())["tax_year"] == 2025
    changed = event()
    changed["amount"] += Decimal("0.000001")
    with pytest.raises(ValueError, match="CASH_OR_DATES"):
        a.ordinary_2025(history, tax, "SPY", changed)


@pytest.mark.parametrize(
    "changes",
    [
        {"Z": "0.01", "N": "0.99", "Q": "0.99"},
        {"O": "0.01", "N": "0.99"},
        {"D": "UNSUPPORTED"},
        {"N": ""},
    ],
)
def test_nonordinary_missing_or_unsupported_status_never_becomes_ordinary(changes):
    history, tax = sheets(changes)
    with pytest.raises(ValueError):
        a.ordinary_2025(history, tax, "SPY", event())


def test_wrong_year_or_incomplete_issuer_universe_is_rejected():
    history, tax = sheets()
    changed = event()
    changed["ex"] = date(2024, 9, 19)
    with pytest.raises(ValueError, match="CLASSIFICATION_YEAR"):
        a.ordinary_2025(history, tax, "SPY", changed)
    with pytest.raises(ValueError, match="EVENT_COVERAGE"):
        a.issuer_2025(history, tax)


def complete():
    return {
        "summary": {
            "expected_execution_sessions": 1003,
            "accepted_complete_execution_sessions": 1003,
            "accepted_daily_sessions": 1003,
            "missing_execution_sessions": 0,
            "missing_daily_sessions": 0,
            "any_requested_but_unrepresented_rows": False,
        },
        "per_asset": {
            t: {
                "daily_sessions": 1003,
                "complete_execution_sessions": 1003,
                "partial_execution_sessions": [],
            }
            for t in a.g.TICKERS
        },
        "proposed_products_not_executed": [],
    }


def test_summary_cannot_hide_one_asset_hole_or_requested_but_absent_rows():
    report = complete()
    a.require_complete(report)
    report["per_asset"]["XLK"]["complete_execution_sessions"] -= 1
    with pytest.raises(ValueError, match="ASSET_ROW_COVERAGE"):
        a.require_complete(report)
    report = complete()
    report["summary"]["any_requested_but_unrepresented_rows"] = True
    with pytest.raises(ValueError, match="CONTINUOUS_ROW_COVERAGE"):
        a.require_complete(report)
    report = complete()
    del report["per_asset"]["SPY"]
    with pytest.raises(ValueError, match="UNIVERSE"):
        a.require_complete(report)


def test_report_is_create_only_and_read_only_verify_compares_every_byte():
    archive = FakeGitHub(a.c.m.DESTINATION)
    files = a.pack({"invented": True}, "b" * 40)
    with pytest.raises(ValueError, match="REPORT_MISSING"):
        a.retain(archive, files, verify_only=True)
    digest = a.retain(archive, files, verify_only=False)
    refs = copy.deepcopy(archive.refs)
    archive.events.clear()
    assert a.retain(archive, files, verify_only=True) == digest
    assert all(method == "GET" for method, _ in archive.events)
    assert archive.refs == refs
    files[next(iter(files))] += b"changed"
    with pytest.raises(ValueError, match="FRESH_REPORT_BYTES"):
        a.retain(archive, files, verify_only=False)


def test_bad_archive_paths_are_rejected_before_writes():
    archive = FakeGitHub(a.c.m.DESTINATION)
    files = a.pack({"invented": True}, "b" * 40)
    files["unexpected.json"] = b"{}"
    with pytest.raises(ValueError, match="PATHS"):
        a.retain(archive, files, verify_only=False)
    assert not archive.refs


def test_cli_failure_does_not_print_exception_body_or_credentials(monkeypatch, capsys):
    monkeypatch.setattr(a.sys, "argv", ["audit", "run", "b" * 40])
    monkeypatch.setattr(a, "guarded", lambda _: None)
    monkeypatch.setattr(a, "build", lambda *_: (_ for _ in ()).throw(ValueError("PRIVATE_CONTENT")))
    assert a.main() == 1
    assert "PRIVATE_CONTENT" not in capsys.readouterr().out
