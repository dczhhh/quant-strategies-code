"""Invented-year workbook fixtures; no fabricated source admission."""

from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest

from tests.test_private_archive_migration import FakeGitHub
from tests.test_real_etf_2025 import documents
from tools import rotation_four_year_annual as a


def fixture(year):
    docs = documents()
    history, tax = [a.c.p.workbook(docs[a.c.p.DOCUMENTS[k]["id"]]["body"]) for k in range(2)]
    history["dividend"][1].update(
        {"D": f"{year}-09-19", "E": f"{year}-09-19", "F": f"{year}-10-31"}
    )
    row = deepcopy(tax["2025 ICI Primary"][-1])
    row.update({"G": f"{year}-09-19", "H": f"{year}-09-19", "I": f"{year}-10-31"})
    index = 13 if year == 2023 else 12
    rows = (
        [{"A": f"SPDR ETF {year} Tax Summary - Primary Layout"}]
        + [{} for _ in range(index - 1)]
        + [a.headers(year), row]
    )
    event = {
        "ex": date(year, 9, 19),
        "record": date(year, 9, 19),
        "pay": date(year, 10, 31),
        "amount": Decimal(1),
    }
    return history, {f"{year} ICI Primary": rows}, event


@pytest.mark.parametrize("year", [2023, 2024])
def test_exact_same_year_components_preserve_copy_provenance(year):
    history, tax, event = fixture(year)
    result = a.fields(year, history, tax, "SPY", event)
    assert result["tax_year"] == year
    assert result["ordinary_fields_complete_in_retained_copy"] is True
    assert result["downloaded_from_issuer_origin"] is False
    assert result["original_issuer_download_hash_independently_attested"] is False
    assert result["PIT_verified"] is result["performance_admission"] is False
    assert set(result["explicit_numeric_fields"]) == set(a.c.a.TAX_NUMBERS)


@pytest.mark.parametrize("year", [2023, 2024])
@pytest.mark.parametrize(
    "kind",
    [
        "title",
        "header",
        "offset",
        "duplicate",
        "cusip",
        "pay",
        "estimated",
        "extended",
        "blank",
        "gain",
        "cash",
    ],
)
def test_corruption_never_becomes_ordinary(year, kind):
    history, tax, event = fixture(year)
    rows = tax[f"{year} ICI Primary"]
    if kind == "title":
        rows[0]["A"] = "SPDR ETF 2025 Tax Summary - Primary Layout"
    elif kind == "header":
        rows[-2]["M"] = "2025 (Current Year) (14+15+22+26+28+30)"
    elif kind == "offset":
        rows.insert(1, {})
    elif kind == "duplicate":
        rows.append(deepcopy(rows[-1]))
    elif kind == "cash":
        event["amount"] += Decimal("0.000001")
    else:
        key, value = {
            "cusip": ("B", "999999999"),
            "pay": ("I", f"{year}-10-30"),
            "estimated": ("D", "E"),
            "extended": ("F", "X"),
            "blank": ("N", ""),
            "gain": ("O", "0.01"),
        }[kind]
        rows[-1][key] = value
    with pytest.raises((ValueError, a.c.p.Blocked)):
        a.fields(year, history, tax, "SPY", event)


@pytest.mark.parametrize("year", [2022, 2025, "2023", True])
def test_no_new_year_or_coerced_year_is_accepted(year):
    with pytest.raises(ValueError, match="ANNUAL_YEAR"):
        a.headers(year)


def test_wrong_event_year_and_missing_coverage_are_rejected():
    history, tax, event = fixture(2023)
    event["ex"] = date(2024, 9, 19)
    with pytest.raises(ValueError, match="EVENT_YEAR"):
        a.fields(2023, history, tax, "SPY", event)
    with pytest.raises(ValueError, match="ANNUAL_COVERAGE"):
        a.audit({}, history)


def test_replaced_candidate_commit_stops_before_blob_reads():
    archive = FakeGitHub(a.c.m.DESTINATION)
    archive.refs[a.REF] = "e" * 40
    with pytest.raises(ValueError, match="CANDIDATE_REF_PIN"):
        a.read(archive)
    assert all("/git/blobs/" not in path for _, path in archive.events)


def test_pinned_manifest_and_body_mutations_stop_before_parsing(monkeypatch):
    archive = FakeGitHub(a.c.m.DESTINATION)
    archive.refs[a.REF] = a.COMMIT
    saved = {a.PREFIX + "manifest.json": b"{}"}
    monkeypatch.setattr(a.c.b, "read", lambda *_args: (saved, a.c.m.BOOTSTRAP))
    with pytest.raises(ValueError, match="MANIFEST_BYTES"):
        a.read(archive)
