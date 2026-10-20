"""Strict same-year fields from retained third-party issuer workbook copies.

Hashes establish retained-byte identity, not authenticity of an issuer original.
No transport, finality, investor tax qualification or historical PIT is attested.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

from tools import rotation_four_year_gap_audit as a

c, u = a.c, a.u
PREFIX = "research/sources/rotation_four_year_annual_candidates_v1/"
REF = "research-data/rotation-four-year-annual-candidates-v1"
COMMIT = "ca9d5c3532877b263e0c2bc73c6931fe0bc8312b"
MANIFEST_BLOB = "576994426d967a0f6b9e9a6d8fdc0712cad52155"
FILES = {
    "issuer-2023-archival-copy.xlsx": (
        279930,
        "2f662aeb7fed87d7f351aa991d5d3ea21746eb1c8935ef7d2a7e39e150612478",
        "625cd047ec1adf05f303fbb059b7fb15c1151f3d",
    ),
    "issuer-2024-archival-copy.xlsx": (
        328410,
        "9a8d49fa20f523b6ecd2f30035314dc230533fe09a590b1ee15b091aabac084b",
        "0443330617002bbf1f718c3db9afe4db2a15cbc1",
    ),
    "sector-2024-ncsr.html.gz": (
        385354,
        "1aa3f42b6b52fb8609b48a587560d7e9081e3756398e6462be23a545c90d2a1d",
        "d0a2293964f7a8cadd7fa09f3f30292e7d89232b",
    ),
}
SOURCE_METADATA = {
    2023: (
        "https://drive.google.com/uc?export=download&id=1-XiMcR9G4-WEHgWQP6Nep53VNlH30b0s",
        "2026-10-19T14:20:55.886642+00:00",
    ),
    2024: (
        "https://drive.google.com/uc?export=download&id=1gC_1fvYGL8sDC6XLvmoo0bn9zeeFj0ZO",
        "2026-10-19T14:20:43.911136+00:00",
    ),
}


def need(ok, code):
    if not ok:
        raise ValueError("FOUR_YEAR_ANNUAL_" + code)


def headers(year):
    need(type(year) is int and year in SOURCE_METADATA, "YEAR")
    result = dict(c.a.TAX_HEADERS)
    result.update(
        {
            "D": "Estimated (E)",
            "F": "Corrected (C)" if year == 2023 else "Corrected (C) or Extended (X)",
            "K": f"{year - 1} (Prior Year)",
            "L": f"{year + 1} (Next Year)",
            "M": f"{year} (Current Year) (14+15+22+26+28+30)",
        }
    )
    return result


def provenance():
    return {
        "provenance": "third_party_archival_copy_of_issuer_workbook",
        "downloaded_from_issuer_origin": False,
        "original_issuer_download_hash_independently_attested": False,
        "transport_request_start_and_finish_clocks_attested": False,
        "observation_clock": "local_body_file_completion_not_transport_or_historical_known_at",
        "document_finality_verified": False,
        "investor_withholding_qualification_verified": False,
        "PIT_verified": False,
        "performance_admission": False,
    }


def read(archive):
    """Read exactly the create-only candidate intake, never request a new URL."""
    archive.visibility(True)
    need(archive.ref(REF) == COMMIT, "CANDIDATE_REF_PIN")
    saved, parent = c.b.read(archive, REF, {PREFIX + x for x in (*FILES, "manifest.json")})
    need(parent == c.m.BOOTSTRAP, "CANDIDATE_PARENT")
    manifest_body = saved[PREFIX + "manifest.json"]
    need(c.m.git_blob(manifest_body) == MANIFEST_BLOB, "MANIFEST_BYTES")
    manifest = json.loads(manifest_body)
    need(
        manifest["schema"] == "rotation_four_year_annual_candidate_intake_v1"
        and manifest["status"] == "RETAINED_UNVERIFIED_CANDIDATE_COPIES"
        and manifest["code_source_commit"] is None
        and manifest["transport_request_start_and_finish_clocks_attested"] is False
        and manifest["existing_unknown_2024_issuer_API_reservation_reset"] is False,
        "MANIFEST_PROVENANCE",
    )
    for name, (size, sha, blob) in FILES.items():
        body = saved[PREFIX + name]
        c.b.secret_free(body, (archive.token,))
        need(
            len(body) == size and c.m.sha256(body) == sha and c.m.git_blob(body) == blob,
            "SOURCE_BYTES",
        )
        need(
            manifest["files"][PREFIX + name]
            == {"bytes": size, "sha256": sha, "git_blob_sha": blob},
            "MANIFEST_SOURCE",
        )
    # The SEC filing corroborates fiscal-year summaries only, not event classification.
    sec = a.g.bounded_gunzip(saved[PREFIX + "sector-2024-ncsr.html.gz"], 16 * 1024 * 1024)
    need(
        len(sec) == 10498759
        and c.m.sha256(sec) == "448d27045106ddea9ef9e44143540e6a3b953b757c1b12aa779676d76f664ddd"
        and sec.rstrip().lower().endswith(b"</html>"),
        "SEC_COMPLETE_BODY",
    )
    result = {}
    for year, (url, observed_at) in SOURCE_METADATA.items():
        name = f"issuer-{year}-archival-copy.xlsx"
        body = saved[PREFIX + name]
        metadata = [d for d in manifest["documents"] if d.get("tax_year") == year]
        need(
            len(metadata) == 1
            and metadata[0]["url"] == url
            and metadata[0]["observed_body_file_complete_at"] == observed_at
            and metadata[0]["downloaded_from_issuer_origin"] is False
            and metadata[0]["original_issuer_download_hash_independently_attested"] is False,
            "SOURCE_PROVENANCE",
        )
        result[year] = {
            "body": body,
            "source": u.ResearchSource(
                f"archival-issuer-tax:{year}",
                c.m.sha256(body),
                datetime.fromisoformat(observed_at),
                url,
            ),
            "intake_commit": COMMIT,
            **provenance(),
        }
    return result


def fields(year, history, tax, ticker, event):
    expected = headers(year)
    need(event["ex"].year == year, "EVENT_YEAR")
    match = a.issuer_match(history, ticker, event)
    rows = tax.get(f"{year} ICI Primary", [])
    index = 13 if year == 2023 else 12
    need(
        len(rows) > index + 1
        and rows[0].get("A") == f"SPDR ETF {year} Tax Summary - Primary Layout"
        and all(rows[index].get(key) == value for key, value in expected.items()),
        "YEAR_TITLE_OR_HEADERS",
    )
    selected = [
        (i, row)
        for i, row in enumerate(rows)
        if row.get("C") == ticker and c.p.issuer_day(row.get("H")) == event["ex"]
    ]
    need(len(selected) == 1, "EVENT_UNIQUENESS")
    ordinal, row = selected[0]
    need(
        row.get("A")
        and row.get("B") == match["rows"][0]["issuer_cells"]["C"]
        and c.p.issuer_day(row.get("G")) == event["record"]
        and c.p.issuer_day(row.get("I")) == event["pay"],
        "IDENTITY_OR_DATES",
    )
    allowed = {"D": (None, ""), "E": (None, "", "R"), "F": (None, "", "C")}
    need(all(row.get(key) in values for key, values in allowed.items()), "STATUS")
    numbers = {key: c.p.number(row.get(key)) for key in c.a.TAX_NUMBERS}
    residuals = {
        "ordinary_components": numbers["Q"] - numbers["N"] - numbers["O"] - numbers["P"],
        "qualified_components": numbers["U"] - numbers["R"] - numbers["S"] - numbers["T"],
        "current_year_components": numbers["M"]
        - sum((numbers[key] for key in ("N", "O", "V", "Z", "AB", "AD")), Decimal(0)),
        "total_vs_current_year": numbers["J"] - numbers["M"],
    }
    tolerance = Decimal("1E-8")
    need(
        all(abs(value) <= tolerance for value in residuals.values())
        and numbers["U"] <= numbers["Q"] + tolerance
        and all(abs(numbers[key] - event["amount"]) <= tolerance for key in ("J", "M", "N", "Q"))
        and all(numbers[key] == 0 for key in ("O", "P", "V", "Z", "AB", "AC", "AD")),
        "NONORDINARY_OR_AMOUNT_COMPONENTS",
    )
    return {
        "status": "SAME_YEAR_ARCHIVAL_ORDINARY_FIELDS_AND_ISSUER_CASH_MATCH",
        "tax_year": year,
        "issuer_history": match,
        "parsed_row_index": ordinal,
        "issuer_cells": row,
        "explicit_numeric_fields": {key: str(value) for key, value in numbers.items()},
        "residuals": {key: str(value) for key, value in residuals.items()},
        "prior_next_year_cells_preserved": {key: row.get(key) for key in ("K", "L")},
        "source_status_flags_preserved": {key: row.get(key) for key in ("D", "E", "F")},
        "ordinary_fields_complete_in_retained_copy": True,
        "engine_invoked": False,
        **provenance(),
    }


def audit(documents, history):
    result = {}
    need(set(documents) == set(SOURCE_METADATA), "ANNUAL_COVERAGE")
    for year in SOURCE_METADATA:
        tax, result[year] = c.p.workbook(documents[year]["body"]), {}
        for ticker in a.g.TICKERS:
            rows = [
                row
                for row in history["dividend"][1:]
                if row.get("B") == ticker and c.p.issuer_day(row["D"]).year == year
            ]
            need(len(rows) == 4, "ANNUAL_EVENT_COVERAGE")
            result[year][ticker] = []
            for row in rows:
                event = {
                    "ex": c.p.issuer_day(row["D"]),
                    "record": c.p.issuer_day(row["E"]),
                    "pay": c.p.issuer_day(row["F"]),
                    "amount": Decimal(row["G"]),
                }
                result[year][ticker].append(fields(year, history, tax, ticker, event))
    return result
