"""Frozen first-batch scopes and structural codecs; no real-data trust issuance."""

from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from importlib.resources import files
from typing import cast

import polars as pl

from quant_constraints.calendar import SessionCalendar

from .archive_contracts import canonical, digest
from .demand import (
    DemandPlan,
    Product,
    UntrustedChain,
    check_secrets,
    deny,
    fixed_url,
    parse_untrusted_page,
)
from .private_repo_ingest import COLUMNS, FILE_LIMIT, VENDOR_COLUMNS, _json

FROZEN_SHA256 = "387612d9dceb0579e62c6220c64d2f62b6845d0bbe963fc216ee3b821b8f638f"
TOTAL_LIMIT = 4 * 1024 * 1024


def frozen_batch() -> dict:
    content = files(__package__).joinpath("resources/sample_batch_v1.json").read_bytes()
    if digest(content) != FROZEN_SHA256:
        deny("FROZEN_SAMPLE_PLAN_CHANGED")
    return _json(content)


@dataclass(frozen=True, slots=True)
class SampleSlice:
    capture_id: str
    symbol: str
    day: date
    start_at: datetime
    end_at: datetime
    expected_minutes: int
    products: tuple[str, ...]
    purpose: str

    @property
    def data_branch(self) -> str:
        return "research-data/" + self.capture_id

    @property
    def data_path(self) -> str:
        return "research/data/" + self.capture_id.replace("-", "_") + "/"

    def source_branch(self, product: str) -> str:
        self.check_product(product)
        return "research-sources/" + self.capture_id + "-" + product

    def source_path(self, product: str) -> str:
        self.check_product(product)
        return "research/sources/" + self.capture_id.replace("-", "_") + "/" + product + "/"

    def check_product(self, product: str) -> None:
        if type(product) is not str or product not in self.products:
            deny("UNAPPROVED_SAMPLE_PRODUCT")

    def wire(self) -> dict:
        result = asdict(self)
        result.update(
            day=self.day.isoformat(),
            start_at=self.start_at.isoformat(),
            end_at=self.end_at.isoformat(),
        )
        result["products"] = list(self.products)
        result.update(
            schema_version="research_private_sample_slice_v1",
            batch_sha256=FROZEN_SHA256,
            evidence_sha256=digest(canonical(security_evidence(self.symbol))),
        )
        return result

    @property
    def plan_sha256(self) -> str:
        return digest(canonical(self.wire()))

    def parser_plan(self) -> DemandPlan:
        # This is only a bounded wire-parser adapter, never a native execution permit.
        return DemandPlan(
            "research_demand_capture_v1",
            self.capture_id,
            "issue_5_batch1",
            "private_github",
            "massive",
            "stocks_starter",
            self.symbol,
            self.start_at,
            self.end_at,
            ("raw", "comparison"),
            5000,
            4,
            FILE_LIMIT,
            TOTAL_LIMIT,
            20,
            1,
            30,
            None,
            None,
        )

    def url(self, product: str) -> str:
        self.check_product(product)
        return fixed_url(self.parser_plan(), cast(Product, product))


def sample_slices() -> tuple[SampleSlice, ...]:
    result = []
    for row in frozen_batch()["slices"]:
        spec = SampleSlice(
            row["capture_id"],
            row["symbol"],
            date.fromisoformat(row["day"]),
            datetime.fromisoformat(row["start_at"]),
            datetime.fromisoformat(row["end_at"]),
            row["expected_minutes"],
            tuple(row["products"]),
            row["purpose"],
        )
        session = SessionCalendar().session(spec.day)
        if (
            session is None
            or spec.start_at != session.market_open
            or spec.end_at != session.market_close
            or spec.expected_minutes != int((spec.end_at - spec.start_at).total_seconds() / 60)
            or spec.start_at.utcoffset() != UTC.utcoffset(spec.start_at)
            or spec.products not in (("raw",), ("raw", "comparison"))
        ):
            deny("SAMPLE_CALENDAR_CONFLICT")
        result.append(spec)
    return tuple(result)


def sample_slice(capture_id: str) -> SampleSlice:
    for spec in sample_slices():
        if type(capture_id) is str and capture_id == spec.capture_id:
            return spec
    deny("UNAPPROVED_SAMPLE_SLICE")


def security_evidence(symbol: str) -> dict:
    for record in frozen_batch()["securities"]:
        if record["symbol"] == symbol:
            return record
    deny("UNREVIEWED_INSTRUMENT_EVIDENCE")


def validate_rows(spec: SampleSlice, rows: list[dict]) -> list[dict]:
    if type(rows) is not list or len(rows) > spec.expected_minutes:
        deny("SAMPLE_RECORD_BOUNDS")
    previous = None
    lower, upper = int(spec.start_at.timestamp() * 1000), int(spec.end_at.timestamp() * 1000)
    for row in rows:
        if (
            type(row) is not dict
            or set(row) != set(COLUMNS)
            or type(row["timestamp_ms"]) is not int
        ):
            deny("SAMPLE_TABLE_SCHEMA_INVALID")
        stamp = row["timestamp_ms"]
        if (
            not lower <= stamp < upper
            or stamp % 60000
            or (previous is not None and stamp <= previous)
        ):
            deny("SAMPLE_BAR_TIME_CONFLICT")
        previous = stamp
        values = [row[c] for c in COLUMNS[1:]]
        if any(type(v) not in (float, int) or not math.isfinite(v) for v in values):
            deny("SAMPLE_BAR_VALUES_INVALID")
        if (
            min(values[:4]) <= 0
            or values[4] < 0
            or not row["low"]
            <= min(row["open"], row["close"])
            <= max(row["open"], row["close"])
            <= row["high"]
        ):
            deny("SAMPLE_BAR_VALUES_INVALID")
    return rows


def source_rows(spec: SampleSlice, content: bytes, product: str) -> list[dict]:
    spec.check_product(product)
    if not 0 < len(content) <= FILE_LIMIT:
        deny("SAMPLE_RESPONSE_BUDGET_EXCEEDED")
    check_secrets(content)
    original = _json(content)
    if "count" in original:
        if type(original["count"]) is not int or original["count"] != original.get("resultsCount"):
            deny("PROVIDER_COUNTS_INVALID")
        parser_content = canonical({k: v for k, v in original.items() if k != "count"})
    else:
        parser_content = content
    page = parse_untrusted_page(parser_content, spec.parser_plan(), cast(Product, product))
    chain = UntrustedChain(spec.parser_plan(), cast(Product, product), fixture=False)
    chain.accept(spec.url(product), page)
    if chain.next_url is not None:
        deny("PAGINATION_UNVERIFIED")
    if re.fullmatch(r"[A-Za-z0-9_-]{1,256}", page["request_id"]) is None:
        deny("SOURCE_METADATA_INVALID")
    for row in page.get("results", []):
        if "otc" in row and row["otc"] is not False:
            deny("SOURCE_METADATA_INVALID")
        if "n" in row and (type(row["n"]) is not int or row["n"] < 0):
            deny("SOURCE_METADATA_INVALID")
        if "vw" in row and (
            type(row["vw"]) not in (int, float) or not math.isfinite(row["vw"]) or row["vw"] < 0
        ):
            deny("SOURCE_METADATA_INVALID")
    return validate_rows(
        spec, [dict(zip(COLUMNS, [r[c] for c in VENDOR_COLUMNS])) for r in page.get("results", [])]
    )


def decode_csv(spec: SampleSlice, content: bytes) -> list[dict]:
    check_secrets(content)
    reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
    if reader.fieldnames != list(COLUMNS):
        deny("SAMPLE_TABLE_SCHEMA_INVALID")
    rows = []
    for row in reader:
        if (
            set(row) != set(COLUMNS)
            or any(v is None for v in row.values())
            or len(rows) >= spec.expected_minutes
        ):
            deny("SAMPLE_TABLE_SCHEMA_INVALID")
        rows.append(
            dict(zip(COLUMNS, [int(row[COLUMNS[0]]), *[float(row[c]) for c in COLUMNS[1:]]]))
        )
    return validate_rows(spec, rows)


def encode_rows(spec: SampleSlice, rows: list[dict]) -> tuple[bytes, bytes]:
    validate_rows(spec, rows)
    normalized = [
        {"timestamp_ms": r["timestamp_ms"], **{c: float(r[c]) for c in COLUMNS[1:]}} for r in rows
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(normalized)
    csv_bytes = output.getvalue().encode()
    schema = {"timestamp_ms": pl.Int64, **dict.fromkeys(COLUMNS[1:], pl.Float64)}
    frame = pl.DataFrame(decode_csv(spec, csv_bytes), schema=schema)
    buffer = io.BytesIO()
    frame.write_parquet(buffer, compression="zstd", statistics=True)
    return csv_bytes, buffer.getvalue()


def structural_checks(spec: SampleSlice, tables: dict[str, list[dict]]) -> dict:
    expected = set(
        range(int(spec.start_at.timestamp() * 1000), int(spec.end_at.timestamp() * 1000), 60000)
    )
    if set(tables) != set(spec.products):
        deny("SAMPLE_PRODUCT_SET_INVALID")
    gaps = {
        p: sorted(expected - {r["timestamp_ms"] for r in validate_rows(spec, rows)})
        for p, rows in tables.items()
    }
    return {
        "schema_version": "research_sample_structural_checks_v1",
        "expected_minutes": spec.expected_minutes,
        "row_counts": {p: len(rows) for p, rows in tables.items()},
        "missing_minutes": gaps,
        "minute_coverage_complete": all(not v for v in gaps.values()),
        "checks_are_structural_only": True,
        "product_values_equal": tables["raw"] == tables["comparison"]
        if "comparison" in tables
        else None,
        "missing_bars_filled": False,
    }
