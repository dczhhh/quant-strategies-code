"""Versioned 2025 ETF captures and retrospective components; never grants real/PIT entry."""

from __future__ import annotations

import json
import os
import re
import runpy
import sys
from datetime import UTC, date, datetime
from decimal import Decimal, localcontext
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, build_opener

p = SimpleNamespace(**runpy.run_path(str(Path(__file__).with_name("real_etf_probe.py"))))
b, m = p.b, p.m
START, END, SPLIT_END = "2025-09-15", "2025-11-03", "2026-10-15"
PREFIX = "research/data/real_etf_2025_v1/"
REPORT_PREFIX = "research/verification/real_etf_2025_v1/"
DOCUMENT_PINS = {
    "research-data/real-etf-issuer-v1-ssga-historical-distributions": "88b8e7d5b89e1c5fa0d436f6f9cb50268e0bc898",
    "research-reservations/real-etf-issuer-v1-ssga-historical-distributions": "2a1e6b1a451f08e5f4daac3d806b7218a9a59052",
    "research-data/real-etf-issuer-v1-ssga-ici-primary-current": "f7c6c0d7fcb4e53faf7b27fbda118ab8f340f826",
    "research-reservations/real-etf-issuer-v1-ssga-ici-primary-current": "5194de845af1a41a582fb0bd342cdbe23a01b848",
}
POLICY = {
    "schema": "real_etf_2025_v1",
    "event_year": 2025,
    "selection": "one September event per existing target; SPY first",
    "maximum_vendor_requests_per_ticker": 4,
    "maximum_response_bytes": m.FILE_LIMIT,
    "new_issuer_requests": 0,
    "dividend_endpoint": "/stocks/v1/dividends",
    "original_amount_field": "cash_amount",
    "current_share_amount_field": "split_adjusted_cash_amount",
    "distribution_type_is_final_tax_classification": False,
    "historical_known_at": None,
    "price_comparison_rounding_usd": "0.0002",
    "issuer_amount_tolerance_usd": "1E-8",
    "signal_oracle_tolerance": "1E-28",
    "signal": "gross_ex_close_reinvestment_hypothesis_not_actual_cash",
    "within_window_splits_supported": False,
    "certification": "UNVERIFIED",
    **b.FALSE_FLAGS,
}
TAX_HEADERS = {
    "A": "Security Description (Fund Name)",
    "B": "CUSIP",
    "C": "Ticker Symbol",
    "D": " Estimated (E)",
    "E": "Reclass (R)",
    "F": "Corrected (C) or Extended (X)",
    "G": "Record Date",
    "H": "Ex-Dividend Date",
    "I": "Payable Date",
    "J": "Total Distribution Per Share (11+12+13)",
    "K": "2024 (Prior Year)",
    "L": "2026 (Next Year)",
    "M": "2025 (Current Year) (14+15+22+26+28+30)",
    "N": "Income Dividends",
    "O": "Short-term Capital Gain",
    "P": "Foreign Tax Paid",
    "Q": "Ordinary Dividends (14+15+16)",
    "R": "Qualified Income Dividends",
    "S": "Qualified Short-term Gains",
    "T": "Qualified Foreign Tax Paid",
    "U": "Qualified Dividends* (18+19+20)",
    "V": "Total Capital Gain Distr.",
    "Z": "Nondividend Distributions",
    "AB": "Cash Liquidation Distr. ",
    "AC": "Noncash Liquidation Distr.",
    "AD": "Exempt Interest Dividends",
}
TAX_NUMBERS = ("J", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "Z", "AB", "AC", "AD")


def products(ticker):
    p.need(type(ticker) is str and ticker in p.TICKERS, "TICKER_OUTSIDE_FIXED_SCOPE")
    rows = []
    for kind, adjusted in (("raw", False), ("split-adjusted", True)):
        rows.append(
            {
                "id": ticker.lower() + "-" + kind,
                "ticker": ticker,
                "kind": "bars",
                "adjusted": adjusted,
                "url": f"https://api.massive.com/v2/aggs/ticker/{ticker}/range/1/day/{START}/{END}?adjusted={str(adjusted).lower()}&sort=asc&limit=5000",
            }
        )
    for kind, endpoint, field, finish in (
        ("dividends", "dividends", "ex_dividend_date", END),
        ("splits", "splits", "execution_date", SPLIT_END),
    ):
        rows.append(
            {
                "id": ticker.lower() + "-" + kind,
                "ticker": ticker,
                "kind": kind,
                "url": f"https://api.massive.com/stocks/v1/{endpoint}?ticker={ticker}&{field}.gte={START}&{field}.lte={finish}&sort={field}.asc&limit=100",
            }
        )
    return tuple(rows)


PLAN_HASH = m.sha256(
    m.canonical(
        {
            "products": {t: products(t) for t in p.TICKERS},
            "document_pins": DOCUMENT_PINS,
            "policy": POLICY,
        }
    )
)


def checked(product):
    p.need(
        any(m.canonical(product) == m.canonical(v) for t in p.TICKERS for v in products(t)),
        "PRODUCT_OUTSIDE_FIXED_SCOPE",
    )


def day(value):
    p.need(type(value) is str and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value), "EVENT_DATE_INVALID")
    return date.fromisoformat(value)


def decode_market(body):
    value = json.loads(
        body,
        object_pairs_hook=b.pairs,
        parse_float=Decimal,
        parse_constant=lambda _: p.need(False, "NUMBER_NONFINITE"),
    )
    p.need(type(value) is dict, "RESPONSE_NOT_OBJECT")
    return value


def market_number(value, *, positive=False):
    p.need(type(value) in (int, Decimal), "MARKET_NUMBER_INVALID")
    return p.number(value, positive=positive)


def market_rows(product, body):
    checked(product)
    value = decode_market(body)
    p.need(
        value.get("status") in {"OK", "DELAYED"} and not value.get("next_url"),
        "RESPONSE_INCOMPLETE",
    )
    rows = value.get("results")
    p.need(type(rows) is list and len(rows) <= 100, "RESULTS_INVALID")
    ticker, kind = product["ticker"], product["kind"]
    if kind == "bars":
        p.need(
            value.get("ticker") == ticker
            and value.get("adjusted") is product["adjusted"]
            and len(rows) >= 2,
            "BAR_IDENTITY_OR_COUNT_INVALID",
        )
        result = {}
        for row in rows:
            p.need(type(row) is dict and type(row.get("t")) is int, "BAR_CLOCK_INVALID")
            at = datetime.fromtimestamp(row["t"] / 1000, UTC).astimezone(p.NY)
            date_ = at.date()
            p.need(
                at.hour == at.minute == at.second == at.microsecond == 0
                and START <= date_.isoformat() <= END
                and date_ not in result,
                "BAR_WINDOW_OR_DUPLICATE_INVALID",
            )
            values = {k: market_number(row.get(k), positive=True) for k in ("o", "h", "l", "c")}
            market_number(row.get("v"))
            p.need(
                values["l"]
                <= min(values["o"], values["c"])
                <= max(values["o"], values["c"])
                <= values["h"],
                "BAR_OHLC_INVALID",
            )
            result[date_] = values["c"]
        p.need(list(result) == sorted(result), "BAR_ORDER_INVALID")
        return result
    result = []
    identities = set()
    for row in rows:
        p.need(type(row) is dict and row.get("ticker") == ticker, "EVENT_TICKER_INVALID")
        identity = row.get("id")
        p.need(
            type(identity) is str and bool(identity) and identity not in identities,
            "EVENT_ID_INVALID",
        )
        identities.add(identity)
        if kind == "splits":
            execution = day(row.get("execution_date"))
            p.need(START <= execution.isoformat() <= SPLIT_END, "SPLIT_WINDOW_INVALID")
            p.need(
                row.get("adjustment_type") in {"forward_split", "reverse_split"},
                "SPLIT_KIND_UNSUPPORTED",
            )
            before = market_number(row.get("split_from"), positive=True)
            after = market_number(row.get("split_to"), positive=True)
            p.need(
                (after > before) if row["adjustment_type"] == "forward_split" else (before > after),
                "SPLIT_RATIO_INVALID",
            )
            result.append({"date": execution, "from": before, "to": after})
        else:
            ex, record, pay = (
                day(row.get(k)) for k in ("ex_dividend_date", "record_date", "pay_date")
            )
            declaration = (
                None if row.get("declaration_date") is None else day(row["declaration_date"])
            )
            p.need(
                START <= ex.isoformat() <= END
                and ex.year == 2025
                and ex <= pay
                and record <= pay
                and (declaration is None or declaration <= ex),
                "DIVIDEND_DATES_INVALID",
            )
            p.need(row.get("currency") == "USD", "DIVIDEND_CURRENCY_INVALID")
            p.need(
                row.get("distribution_type")
                in {"recurring", "special", "supplemental", "irregular", "unknown"},
                "DIVIDEND_SCHEMA_INVALID",
            )
            result.append(
                {
                    "ex": ex,
                    "record": record,
                    "pay": pay,
                    "amount": market_number(row.get("cash_amount"), positive=True),
                    "split_amount": market_number(
                        row.get("split_adjusted_cash_amount"), positive=True
                    ),
                    "declaration": declaration,
                    "vendor_recurrence_type": row["distribution_type"],
                }
            )
    if kind == "dividends":
        p.need(len(result) == 1 and result[0]["ex"].month == 9, "DIVIDEND_EVENT_SCOPE_INVALID")
    else:
        dates = [row["date"] for row in result]
        p.need(dates == sorted(set(dates)), "SPLIT_ORDER_OR_DUPLICATE_INVALID")
    return result


def structure(product, body, status):
    if status != 200:
        return False
    try:
        market_rows(product, body)
        return True
    except Exception:
        return False


def ref(product, budget=False):
    checked(product)
    return (
        ("research-reservations/" if budget else "research-data/")
        + "real-etf-2025-v1-"
        + product["id"]
    )


def reservation(product, source):
    checked(product)
    return m.canonical(
        {
            "schema": "real_etf_2025_reservation_v1",
            "source_commit": m.sha(source),
            "plan_sha256": PLAN_HASH,
            "product": product,
            "policy": POLICY,
            "single_attempt": True,
            "unknown_outcome_must_not_be_reset": True,
        }
    )


def fetch_product(product, key):
    checked(product)
    p.need(
        type(key) is str and 0 < len(key) <= 4096 and not any(c.isspace() for c in key),
        "CREDENTIAL_UNAVAILABLE",
    )
    request = Request(
        product["url"], headers={"Authorization": "Bearer " + key, "Accept": "application/json"}
    )
    try:
        with build_opener(m.NoRedirect()).open(request, timeout=45) as response:
            status, body = response.status, response.read(m.FILE_LIMIT + 1)
    except HTTPError as error:
        with error:
            status, body = error.code, error.read(m.FILE_LIMIT + 1)
    b.secret_free(body, (key,))
    return status, body


def load_product(destination, product):
    prefix = PREFIX + product["id"] + "/"
    files, parent = b.read(
        destination, ref(product), {prefix + n for n in ("response.bin", "receipt.json")}
    )
    held, bootstrap = b.read(destination, ref(product, True), {prefix + "reservation.json"})
    receipt = b.decode(files[prefix + "receipt.json"])
    body = files[prefix + "response.bin"]
    b.secret_free(body, (destination.token,))
    p.need(
        parent == destination.ref(ref(product, True))
        and bootstrap == m.BOOTSTRAP
        and held[prefix + "reservation.json"] == reservation(product, receipt["source_commit"])
        and receipt["schema"] == "real_etf_2025_receipt_v1"
        and receipt["plan_sha256"] == PLAN_HASH
        and receipt["product"] == product
        and receipt["policy"] == POLICY
        and receipt["body_sha256"] == m.sha256(body)
        and receipt["body_bytes"] == len(body)
        and type(receipt["http_status"]) is int
        and 100 <= receipt["http_status"] <= 599,
        "CAPTURE_READBACK_INVALID",
    )
    started, finished = (
        datetime.fromisoformat(receipt[k]) for k in ("request_started_at", "response_received_at")
    )
    p.need(
        started.utcoffset() is not None
        and finished.utcoffset() is not None
        and started <= finished,
        "CAPTURE_CLOCK_INVALID",
    )
    return {
        "body": body,
        "receipt": receipt,
        "commit": destination.ref(ref(product)),
        "structure_accepted": structure(product, body, receipt["http_status"]),
    }


def capture(destination, ticker, source, key_factory, *, verify_only=False, fetch=fetch_product):
    captured, made = {}, 0
    for product in products(ticker):
        if destination.ref(ref(product)) is None:
            p.need(not verify_only, "CAPTURE_MISSING")
            p.need(destination.ref(ref(product, True)) is None, "UNKNOWN_REQUEST_OUTCOME_RETAINED")
            key = key_factory()
            p.need(
                type(key) is str and 0 < len(key) <= 4096 and not any(c.isspace() for c in key),
                "CREDENTIAL_UNAVAILABLE",
            )
            prefix = PREFIX + product["id"] + "/"
            budget = b.create(
                destination,
                ref(product, True),
                {prefix + "reservation.json": reservation(product, source)},
                m.BOOTSTRAP,
            )
            started = datetime.now(UTC).isoformat()
            status, body = fetch(product, key)
            made += 1
            b.secret_free(body, (key, destination.token))
            p.need(type(status) is int and 100 <= status <= 599, "HTTP_STATUS_INVALID")
            receipt = {
                "schema": "real_etf_2025_receipt_v1",
                "source_commit": source,
                "product": product,
                "policy": POLICY,
                "plan_sha256": PLAN_HASH,
                "http_status": status,
                "request_started_at": started,
                "response_received_at": datetime.now(UTC).isoformat(),
                "body_bytes": len(body),
                "body_sha256": m.sha256(body),
            }
            b.create(
                destination,
                ref(product),
                {prefix + "response.bin": body, prefix + "receipt.json": m.canonical(receipt)},
                budget,
            )
        captured[
            product["kind"]
            if product["kind"] != "bars"
            else ("split-adjusted" if product["adjusted"] else "raw")
        ] = load_product(destination, product)
    return captured, made


class PinnedIssuerArchive:
    def __init__(self, destination):
        self.destination, self.token = destination, destination.token

    def ref(self, name):
        p.need(name in DOCUMENT_PINS, "ISSUER_REF_OUTSIDE_PINNED_SCOPE")
        return DOCUMENT_PINS[name]

    def request(self, method, suffix, payload=None):
        p.need(method == "GET" and payload is None, "ISSUER_WRITE_FORBIDDEN")
        return self.destination.request(method, suffix)

    def blob(self, sha, size):
        return self.destination.blob(sha, size)


def issuer_documents(destination):
    view = PinnedIssuerArchive(destination)
    docs = {doc["id"]: p.read_document(view, doc) for doc in p.DOCUMENTS[:2]}
    p.need(all(d["receipt"]["http_status"] == 200 for d in docs.values()), "ISSUER_HTTP_UNVERIFIED")
    return docs


def tax_fields(sheets, ticker, event, history):
    rows = sheets.get("2025 ICI Primary", [])
    p.need(
        len(rows) > 13 and rows[0].get("A") == "SPDR ETF 2025 Tax Summary - Primary Layout",
        "TAX_YEAR_OR_TITLE_INVALID",
    )
    p.need(all(rows[12].get(k) == v for k, v in TAX_HEADERS.items()), "TAX_HEADERS_INVALID")
    selected = [
        (i, r)
        for i, r in enumerate(rows)
        if r.get("C") == ticker and p.issuer_day(r.get("H")) == event["ex"]
    ]
    p.need(
        len(selected) == 1 and history["status"] == "ISSUER_HISTORY_FIELD_MATCH",
        "TAX_EVENT_OR_HISTORY_UNVERIFIED",
    )
    ordinal, row = selected[0]
    historical = history["rows"][0]["issuer_cells"]
    p.need(
        row.get("A")
        and row.get("B") == historical["C"]
        and p.issuer_day(row.get("G")) == event["record"]
        and p.issuer_day(row.get("I")) == event["pay"],
        "TAX_IDENTITY_OR_DATE_CONFLICT",
    )
    fields = {k: p.number(row.get(k)) for k in TAX_NUMBERS}
    residuals = {
        "total_vs_original_cash_amount": fields["J"] - event["amount"],
        "ordinary_components": fields["Q"] - fields["N"] - fields["O"] - fields["P"],
        "qualified_components": fields["U"] - fields["R"] - fields["S"] - fields["T"],
        "current_year_components": fields["M"]
        - sum((fields[k] for k in ("N", "O", "V", "Z", "AB", "AD")), Decimal(0)),
        "observed_total_vs_current_year": fields["J"] - fields["M"],
    }
    p.need(
        all(abs(v) <= Decimal("1E-8") for v in residuals.values())
        and fields["U"] <= fields["Q"] + Decimal("1E-8"),
        "TAX_COMPONENT_CONFLICT",
    )
    return {
        "status": "EVENT_YEAR_TAX_FIELDS_MATCH_NOT_FINALITY",
        "tax_year": 2025,
        "parsed_row_index": ordinal,
        "issuer_cells": row,
        "explicit_numeric_fields": {k: str(v) for k, v in fields.items()},
        "residuals": {k: str(v) for k, v in residuals.items()},
        "prior_next_year_cells_preserved": {k: row.get(k) for k in ("K", "L")},
        "source_status_flags_preserved": {k: row.get(k) for k in ("D", "E", "F")},
        "document_finality_verified": False,
        "independent_original_share_basis_verified": False,
        "historical_PIT_verified": False,
        "investor_withholding_qualification_verified": False,
        "ordinary_cash_admission": False,
    }


def components(ticker, captured, documents):
    p.need(all(c["structure_accepted"] for c in captured.values()), "CAPTURE_STRUCTURE_UNVERIFIED")
    parsed = {}
    for product in products(ticker):
        kind = product["kind"]
        if kind == "bars":
            kind = "split-adjusted" if product["adjusted"] else "raw"
        parsed[kind] = market_rows(product, captured[kind]["body"])
    raw, adjusted, dividends, splits = (
        parsed[k] for k in ("raw", "split-adjusted", "dividends", "splits")
    )
    p.need(all(s["date"].isoformat() > END for s in splits), "SPLIT_INSIDE_WINDOW_UNSUPPORTED")
    scale = Decimal(1)
    for split in splits:
        scale *= split["from"] / split["to"]
    p.need(set(raw) == set(adjusted), "BAR_TIMELINE_CONFLICT")
    p.need(day(START) in raw and day(END) in raw, "PRICE_WINDOW_BOUNDARY_MISSING")
    p.need(
        all(abs(adjusted[d] - raw[d] * scale) <= Decimal("0.0002") for d in raw),
        "PRICE_SPLIT_BASIS_CONFLICT",
    )
    event = dividends[0]
    p.need(event["pay"] in raw, "PAY_BOUNDARY_MISSING")
    p.need(
        abs(event["split_amount"] - event["amount"] * scale) <= Decimal("1E-8"),
        "DIVIDEND_SPLIT_BASIS_CONFLICT",
    )
    history_sheets = p.workbook(documents[p.DOCUMENTS[0]["id"]]["body"])
    primary_sheets = p.workbook(documents[p.DOCUMENTS[1]["id"]]["body"])
    history = p.history_match(history_sheets, ticker, dividends)
    p.need(history["status"] == "ISSUER_HISTORY_FIELD_MATCH", "ISSUER_HISTORY_CONFLICT")
    tax = tax_fields(primary_sheets, ticker, event, history)
    signal = p.return_probe(raw, adjusted, dividends)
    return {
        "status": "SAME_YEAR_RETROSPECTIVE_COMPONENTS_PASS",
        "event_year": 2025,
        "issuer_history": history,
        "tax_fields": tax,
        "signal": signal,
        "observed_split_scale": str(scale),
        "vendor_recurrence_type_not_tax_class": event["vendor_recurrence_type"],
        "declaration_date_not_historical_known_at": None
        if event["declaration"] is None
        else event["declaration"].isoformat(),
        "classification": "ISSUER_FINALITY_AND_PIT_UNVERIFIED",
        "actual_broker_cash_verified": False,
        "native_engine_verified": False,
        "intraday_execution_verified": False,
        "settlement_lifecycle_verified": False,
        "performance_admission": False,
        "certification": "UNVERIFIED",
        **b.FALSE_FLAGS,
    }


def public_summary(value):
    case = value["case"]
    return {
        "schema": "real_etf_2025_summary_v1",
        "ticker": value["ticker"],
        "event_year": 2025,
        "plan_sha256": PLAN_HASH,
        "component_status": case["status"],
        "component_reason": case.get("reason"),
        "issuer_history": case.get("issuer_history", {}).get("status", "UNVERIFIED"),
        "tax_fields": case.get("tax_fields", {}).get("status", "UNVERIFIED"),
        "signal": case.get("signal", {}).get("status", "UNVERIFIED"),
        "signal_checkpoints": case.get("signal", {}).get("checkpoints", 0),
        "classification": "ISSUER_FINALITY_AND_PIT_UNVERIFIED",
        "actual_broker_cash_verified": False,
        "native_engine_verified": False,
        "settlement_lifecycle_verified": False,
        "performance_admission": False,
        "full_real_scenarios_accepted": 0,
        "certification": "UNVERIFIED",
        **b.FALSE_FLAGS,
    }


def execute(
    destination,
    ticker,
    source,
    key_factory,
    *,
    verify_only=False,
    fetch=fetch_product,
    load_documents=issuer_documents,
):
    products(ticker)
    m.sha(source)
    destination.visibility(True)
    documents = load_documents(destination)
    captured, made = capture(
        destination, ticker, source, key_factory, verify_only=verify_only, fetch=fetch
    )
    try:
        with localcontext() as ctx:
            ctx.prec = 60
            case = components(ticker, captured, documents)
    except Exception as error:
        code = str(error) if isinstance(error, p.Blocked) else "COMPONENT_CHECK_FAILED"
        if re.fullmatch(r"[A-Z_]{1,80}", code) is None:
            code = "COMPONENT_CHECK_FAILED"
        case = {"status": "SAME_YEAR_COMPONENTS_UNVERIFIED", "reason": code}
    value = {
        "schema": "real_etf_2025_report_v1",
        "source_commit": source,
        "ticker": ticker,
        "plan_sha256": PLAN_HASH,
        "policy": POLICY,
        "inputs": {
            k: {
                "receipt": v["receipt"],
                "commit": v["commit"],
                "structure_accepted": v["structure_accepted"],
            }
            for k, v in captured.items()
        },
        "issuer_documents": {
            k: {"commit": v["commit"], "receipt": v["receipt"], "historical_known_at": None}
            for k, v in documents.items()
        },
        "case": case,
    }
    summary = public_summary(value)
    prefix, reference = (
        REPORT_PREFIX + ticker.lower() + "/" + source + "/",
        "research-data/real-etf-2025-probe-v1-" + ticker.lower() + "-" + source,
    )
    files = {
        prefix + "report.json": m.canonical(value),
        prefix + "summary.json": m.canonical(summary),
    }
    if destination.ref(reference) is None:
        p.need(not verify_only, "REPORT_MISSING")
        b.create(destination, reference, files, m.BOOTSTRAP)
    saved, parent = b.read(destination, reference, set(files))
    p.need(parent == m.BOOTSTRAP and saved == files, "REPORT_READBACK_MISMATCH")
    destination.visibility(True)
    return {
        **summary,
        "vendor_requests": made,
        "official_requests": 0,
        "report_sha256": m.sha256(files[prefix + "report.json"]),
    }


def guarded():
    source = m.sha(os.environ.get("REAL_ETF_PROBE_REVIEWED_SHA", ""))
    p.need(
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and os.environ.get("GITHUB_REPOSITORY") == m.CODE
        and os.environ.get("GITHUB_ACTOR") == "dczhhh"
        and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        and os.environ.get("GITHUB_REF") == "refs/heads/main"
        and os.environ.get("GITHUB_SHA") == source
        and os.environ.get("REAL_ETF_PROBE_ENABLED") == "true"
        and os.environ.get("ACTIONS_STEP_DEBUG", "").lower() != "true"
        and os.environ.get("ACTIONS_RUNNER_DEBUG", "").lower() != "true",
        "EXECUTION_NOT_AUTHORIZED",
    )
    m.GitHub(m.CODE).visibility(False)
    return source


def main():
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "matrix":
            p.need(sys.argv[2] in {"SPY", "all"}, "INVALID_SCOPE")
            print("tickers=" + json.dumps(["SPY"] if sys.argv[2] == "SPY" else list(p.TICKERS)))
            return 0
        p.need(len(sys.argv) == 3 and sys.argv[1] in {"capture", "verify"}, "INVALID_COMMAND")
        source = guarded()
        verify = sys.argv[1] == "verify"
        destination = m.GitHub(
            m.DESTINATION, os.environ.get("ARCHIVE_WRITE_TOKEN", ""), writable=not verify
        )
        print(
            json.dumps(
                execute(
                    destination,
                    sys.argv[2],
                    source,
                    lambda: os.environ.get("MASSIVE_API_KEY", ""),
                    verify_only=verify,
                ),
                separators=(",", ":"),
            )
        )
        return 0
    except Exception as error:
        code = str(error) if isinstance(error, p.Blocked) else "CHECK_FAILED"
        if re.fullmatch(r"[A-Z_]{1,80}", code) is None:
            code = "CHECK_FAILED"
        print(
            json.dumps(
                {"status": "REAL_ETF_2025_STOPPED", "code": code, "certification": "UNVERIFIED"},
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
