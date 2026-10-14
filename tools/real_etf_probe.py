"""Issuer files and 36 immutable ETF captures; retrospective math never grants PIT."""

from __future__ import annotations

import io
import json
import os
import re
import runpy
import sys
import zipfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, build_opener
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

p = SimpleNamespace(**runpy.run_path(str(Path(__file__).with_name("real_action_capture.py"))))
r = SimpleNamespace(**runpy.run_path(str(Path(__file__).with_name("real_action_recovery.py"))))
b, m = p.b, p.m
INPUT_COMMITS = {
    "research-data/real-actions-recovery-v1-etf-1-xlb-dividends": "687497e5750b1d3de4b553b1a61920480a2ef54e",
    "research-data/real-actions-recovery-v1-etf-1-xlb-split-adjusted": "ae57af10887e4e7ed54d6e92c25fe7a83bf95982",
    "research-data/real-actions-recovery-v1-etf-1-xlc-dividends": "f207b31a70b2b818a359fd8759f8af4bc5ba37e2",
    "research-data/real-actions-recovery-v1-etf-1-xlc-raw": "225876bf490b2a3407d86ce83aa82776b25d8c8f",
    "research-data/real-actions-recovery-v1-etf-1-xlc-split-adjusted": "0cf0aede3405ef1f9ffca573ceeb712862962b6a",
    "research-data/real-actions-recovery-v1-etf-1-xle-dividends": "ebe3678789c6adbafaae83b7e940364d1c80b3cd",
    "research-data/real-actions-recovery-v1-etf-1-xle-raw": "7c72fe235d125bd183676cfaed74c24037dbbd73",
    "research-data/real-actions-recovery-v1-etf-1-xle-split-adjusted": "9fb889fde650df282ae97c6f8b51904eb0e9d463",
    "research-data/real-actions-v1-etf-1-xlb-raw": "c253894155bfea290234a359e96d9b67613a4b1a",
    "research-data/real-actions-v1-etf-2-xlf-dividends": "b721bb1a6d32e889cdb5662381c787a113e376d7",
    "research-data/real-actions-v1-etf-2-xlf-raw": "4657f91cfaae61f7ece2edfedcbf04e641f59537",
    "research-data/real-actions-v1-etf-2-xlf-split-adjusted": "15e654b1ecc065092ac5a4f296c6664ab8df5664",
    "research-data/real-actions-v1-etf-2-xli-dividends": "cdeb01795b4a3db4091440d96ee779a9579bc97a",
    "research-data/real-actions-v1-etf-2-xli-raw": "69ea7ff58e886c3af50d7911c5237919a603bc01",
    "research-data/real-actions-v1-etf-2-xli-split-adjusted": "dbea3e060284af3714c0538a3031ab65a0526abb",
    "research-data/real-actions-v1-etf-2-xlk-dividends": "ed0062d907874e4720272813a273c3bb8700f59c",
    "research-data/real-actions-v1-etf-2-xlk-raw": "c5c6e13e335b12d209eba44738ffe713c142de68",
    "research-data/real-actions-v1-etf-2-xlk-split-adjusted": "e8969efd79e30d57cd5ef3feb79f236e4240c2a6",
    "research-data/real-actions-v1-etf-3-xlp-dividends": "dd2ec15d4e92aa9d9302bd95317e2689a37f4533",
    "research-data/real-actions-v1-etf-3-xlp-raw": "414928556514b6e7b9763526ddf004ac15feddc6",
    "research-data/real-actions-v1-etf-3-xlp-split-adjusted": "0037b7884049910708bf38c903d9d9628482ce44",
    "research-data/real-actions-v1-etf-3-xlre-dividends": "ac2310d4ea04dd848258b91abbb95af4bffaf9c8",
    "research-data/real-actions-v1-etf-3-xlre-raw": "e9c3c139511843be46666d7d9fb2e6db05e760e2",
    "research-data/real-actions-v1-etf-3-xlre-split-adjusted": "c4dfa3032ec07e094cfe027ca03b30346dad4ec9",
    "research-data/real-actions-v1-etf-3-xlu-dividends": "017cdc14aa5b58bc8478bdc51ec1e8f1c24fd88d",
    "research-data/real-actions-v1-etf-3-xlu-raw": "0a31e10ea1740bb24df11bfc0019c8a590b5ce88",
    "research-data/real-actions-v1-etf-3-xlu-split-adjusted": "b6d9cb7bbce95ea2f6a581f4786993bb9fdfe4a0",
    "research-data/real-actions-v1-etf-4-spy-dividends": "97059f3d84c10e8eb931c854f92f1c1bbfb2d4f1",
    "research-data/real-actions-v1-etf-4-spy-raw": "a1ecccaa6a2e0ed770d8f55b19bdde278af8216a",
    "research-data/real-actions-v1-etf-4-spy-split-adjusted": "b32b97b911619e8638bf385de02be02d6a9887a9",
    "research-data/real-actions-v1-etf-4-xlv-dividends": "c875c333bbf2b7976c89a5babe729df9e6a8c62e",
    "research-data/real-actions-v1-etf-4-xlv-raw": "628b65e84b7f185e629caad3bdbf07c6f5233a9e",
    "research-data/real-actions-v1-etf-4-xlv-split-adjusted": "91668b6857fedcd1f028cb16f78b7ee4e3de0929",
    "research-data/real-actions-v1-etf-4-xly-dividends": "9de05bf51b00800ffd078c18a776f955977a4afb",
    "research-data/real-actions-v1-etf-4-xly-raw": "0ae62891b79c5f2487118894b3d62d92b794361c",
    "research-data/real-actions-v1-etf-4-xly-split-adjusted": "09282b676fdfdc584b0c804739b57d0be9598f2d",
}
TICKERS = ("XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY", "SPY")
PREFIX = "research/verification/real_etf_probe_v1/"
DOCUMENT_PREFIX = "research/sources/real_etf_issuer_v1/"
DOCUMENTS = (
    {
        "id": "ssga-historical-distributions",
        "url": "https://www.ssga.com/library-content/products/fund-data/etfs/us/spdr-etf-historical-distributions.xlsx",
    },
    {
        "id": "ssga-ici-primary-current",
        "url": "https://www.ssga.com/library-content/products/fund-docs/etfs/us/tax-documents/SPDR_ICI_Tax_Summary_Primary.xlsx",
    },
    {
        "id": "ssga-primary-2024-fullname",
        "url": "https://www.ssga.com/api/documents/by-fullname/General%20Documents/2024%20Tax%20Summary%20%28Primary%29",
    },
)
DOC_LIMIT = 8388608
XML_LIMIT = 33554432
POLICY = {
    "schema": "real_etf_issuer_probe_v1",
    "maximum_document_bytes": DOC_LIMIT,
    "maximum_XML_bytes": XML_LIMIT,
    "maximum_products": 3,
    "vendor_requests": 0,
    "historical_known_at": None,
    "certification": "UNVERIFIED",
    "broker_credit_available": False,
    "intraday_execution_available": False,
    "signal": "gross_ex_close_reinvestment_hypothesis_not_issuer_NAV_total_return",
    **b.FALSE_FLAGS,
}
PLAN_HASH = m.sha256(
    m.canonical({"documents": DOCUMENTS, "input_commits": INPUT_COMMITS, "policy": POLICY})
)
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NY = ZoneInfo("America/New_York")


class Blocked(ValueError):
    pass


def need(condition, code):
    if not condition:
        raise Blocked(code)


def number(value, *, positive=False, nonnegative=True):
    need(type(value) in (int, float, str, Decimal), "INVALID_NUMBER")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise Blocked("INVALID_NUMBER") from error
    need(
        result.is_finite() and (result > 0 if positive else not nonnegative or result >= 0),
        "INVALID_NUMBER",
    )
    return result


def xml(data):
    need(
        b"<!DOCTYPE" not in data.upper() and b"<!ENTITY" not in data.upper(), "XML_ENTITY_FORBIDDEN"
    )
    return ET.fromstring(data)


def workbook(body):
    """Bounded read-only extraction. Formula cells never become trusted cached facts."""
    need(len(body) <= DOC_LIMIT, "DOCUMENT_TOO_LARGE")
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        entries = z.infolist()
        need(
            len(entries) <= 512 and len({v.filename for v in entries}) == len(entries),
            "ZIP_INVALID",
        )
        need(sum(v.file_size for v in entries) <= XML_LIMIT, "ZIP_EXPANSION_LIMIT")
        need(
            all(not v.flag_bits & 1 and ".." not in v.filename.split("/") for v in entries),
            "ZIP_INVALID",
        )
        names = z.namelist()
        need(
            "xl/workbook.xml" in names and "xl/_rels/workbook.xml.rels" in names, "WORKBOOK_INVALID"
        )
        strings = []
        if "xl/sharedStrings.xml" in names:
            strings = [
                "".join(n.itertext())
                for n in xml(z.read("xl/sharedStrings.xml")).findall(NS + "si")
            ]
        styles = xml(z.read("xl/styles.xml")) if "xl/styles.xml" in names else None
        custom = (
            {}
            if styles is None
            else {
                int(n.attrib["numFmtId"]): n.attrib["formatCode"]
                for n in styles.findall(NS + "numFmts/" + NS + "numFmt")
            }
        )
        formats = (
            []
            if styles is None
            else [
                int(n.attrib.get("numFmtId", "0"))
                for n in styles.findall(NS + "cellXfs/" + NS + "xf")
            ]
        )
        root = xml(z.read("xl/workbook.xml"))
        props = root.find(NS + "workbookPr")
        epoch = (
            datetime(1904, 1, 1)
            if props is not None and props.attrib.get("date1904") in {"1", "true"}
            else datetime(1899, 12, 30)
        )
        rels = {
            n.attrib["Id"]: n.attrib["Target"]
            for n in xml(z.read("xl/_rels/workbook.xml.rels"))
            if n.attrib.get("TargetMode") != "External"
        }
        sheets = {}
        for sheet in root.findall(NS + "sheets/" + NS + "sheet"):
            rid = sheet.attrib[
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
            ]
            target = rels.get(rid, "")
            path = target.lstrip("/") if target.startswith("/") else "xl/" + target
            need(path in names and ".." not in path.split("/"), "WORKSHEET_TARGET_INVALID")
            rows = []
            for row in xml(z.read(path)).findall(NS + "sheetData/" + NS + "row"):
                values = {}
                for cell in row.findall(NS + "c"):
                    ref = cell.attrib.get("r", "")
                    need(
                        re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]{0,6}", ref) is not None,
                        "CELL_REFERENCE_INVALID",
                    )
                    col = ref.rstrip("0123456789")
                    need(col not in values, "DUPLICATE_CELL")
                    kind, node = cell.attrib.get("t"), cell.find(NS + "v")
                    value = "" if node is None else node.text or ""
                    if cell.find(NS + "f") is not None:
                        values[col] = {"formula": True}
                        continue
                    if kind == "s":
                        need(value.isdigit() and int(value) < len(strings), "SHARED_STRING_INVALID")
                        value = strings[int(value)]
                    elif kind == "inlineStr":
                        inline = cell.find(NS + "is")
                        value = "" if inline is None else "".join(inline.itertext())
                    elif kind == "e":
                        value = {"error": value}
                    elif value and kind not in {"str", "b", "d"}:
                        style = int(cell.attrib.get("s", "0"))
                        fmt = formats[style] if style < len(formats) else 0
                        is_date = fmt in set(range(14, 23)) | set(range(45, 48)) or (
                            fmt in custom
                            and re.search(
                                r"[dmy]", re.sub(r'"[^"]*"|\\.|\[[^\]]*\]', "", custom[fmt]), re.I
                            )
                            is not None
                        )
                        if is_date:
                            numeric = number(value)
                            need(0 <= numeric <= 100000, "DATE_SERIAL_INVALID")
                            value = (epoch + timedelta(days=float(numeric))).date().isoformat()
                        else:
                            number(value, nonnegative=False)  # preserve signed source facts
                    values[col] = value
                rows.append(values)
            need(sheet.attrib["name"] not in sheets, "DUPLICATE_SHEET")
            sheets[sheet.attrib["name"]] = rows
        need(sheets, "EMPTY_WORKBOOK")
        return sheets


def schema_summary(sheets):
    """Only labels/coverage; no source amounts or paid price rows in public logs."""
    result = []
    for name, rows in sheets.items():
        labels = []
        years = set()
        coverage = set()
        for row in rows:
            for value in row.values():
                if not isinstance(value, str):
                    continue
                if value.strip() in TICKERS:
                    coverage.add(value.strip())
                if re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}", value):
                    years.add(value[:4])
        for row in rows[:8]:
            labels.append(
                {
                    key: value[:180]
                    for key, value in row.items()
                    if isinstance(value, str) and not re.fullmatch(r"[-+0-9.,% $()]+", value)
                }
            )
        result.append(
            {
                "sheet": name,
                "row_count": len(rows),
                "header_labels": labels,
                "target_tickers": sorted(coverage),
                "date_years": sorted(years),
            }
        )
    return result


class PinnedArchive:
    def __init__(self, destination):
        self.destination, self.token = destination, destination.token
        self.requests, self.blobs, self.parents = {}, {}, {}

    def request(self, method, suffix, payload=None):
        need(method == "GET" and payload is None, "INPUT_WRITE_FORBIDDEN")
        if suffix not in self.requests:
            self.requests[suffix] = self.destination.request(method, suffix)
        return self.requests[suffix]

    def blob(self, sha, size):
        if (sha, size) not in self.blobs:
            self.blobs[sha, size] = self.destination.blob(sha, size)
        return self.blobs[sha, size]

    def ref(self, name):
        if name in INPUT_COMMITS:
            return INPUT_COMMITS[name]
        source_ref = name.replace("research-reservations/", "research-data/", 1)
        need(source_ref in INPUT_COMMITS and source_ref != name, "UNREVIEWED_INPUT_REF")
        if source_ref not in self.parents:
            commit = self.request("GET", "/git/commits/" + INPUT_COMMITS[source_ref])
            need(commit is not None and len(commit["parents"]) == 1, "INPUT_PARENT_INVALID")
            self.parents[source_ref] = m.sha(commit["parents"][0]["sha"])
        return self.parents[source_ref]

    def visibility(self, private):
        need(private is True, "PRIVATE_DESTINATION_REQUIRED")
        self.destination.visibility(True)


def inputs(destination):
    view = PinnedArchive(destination)
    result = {}
    for group in ("etf-1", "etf-2", "etf-3", "etf-4"):
        for product in p.products(group):
            recovered = product in r.PRODUCTS
            source_ref = r.ref(product) if recovered else p.refs(product)
            prefix = (r.PREFIX if recovered else p.PREFIX) + product["id"] + "/"
            check = (
                r.verify_product(view, product)
                if recovered
                else p.verify_product(view, product, "0" * 40)
            )
            need(check["structure_accepted"] is True, "CAPTURE_STRUCTURE_UNVERIFIED")
            payload, _ = b.read(
                view,
                source_ref,
                {prefix + x for x in ("response.bin", "receipt.json", "checks.json")},
            )
            receipt = b.decode(payload[prefix + "receipt.json"])
            result[product["id"]] = {
                "body": payload[prefix + "response.bin"],
                "product": product,
                "receipt": receipt,
                "commit": INPUT_COMMITS[source_ref],
            }
    need(len(result) == 36, "INPUT_SCOPE_MISMATCH")
    return result


def document_ref(doc, budget=False):
    need(doc in DOCUMENTS, "UNREVIEWED_DOCUMENT")
    return (
        ("research-reservations/" if budget else "research-data/")
        + "real-etf-issuer-v1-"
        + doc["id"]
    )


def fetch_document(doc):
    need(doc in DOCUMENTS, "UNREVIEWED_DOCUMENT")
    request = Request(
        doc["url"],
        headers={
            "Accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "User-Agent": "Noncommercial research verification contact github.com/dczhhh",
        },
    )
    try:
        with build_opener(m.NoRedirect()).open(request, timeout=45) as response:
            status, body = response.status, response.read(DOC_LIMIT + 1)
    except HTTPError as error:
        with error:
            status, body = error.code, error.read(DOC_LIMIT + 1)
    need(len(body) <= DOC_LIMIT, "DOCUMENT_TOO_LARGE")
    return status, body


def reservation(doc, source):
    return m.canonical(
        {
            "schema": "real_etf_issuer_reservation_v1",
            "plan_sha256": PLAN_HASH,
            "source_commit": m.sha(source),
            "document": doc,
            "policy": POLICY,
            "single_attempt": True,
            "unknown_outcome_must_not_be_reset": True,
        }
    )


def read_document(destination, doc):
    path = DOCUMENT_PREFIX + doc["id"] + "/"
    ref = document_ref(doc)
    commit = destination.ref(ref)
    need(commit is not None, "DOCUMENT_MISSING")
    git = destination.request("GET", "/git/commits/" + commit)
    need(git is not None and len(git["parents"]) == 1, "DOCUMENT_PARENT_INVALID")
    tree = destination.request("GET", "/git/trees/" + git["tree"]["sha"] + "?recursive=1")
    need(tree is not None and tree.get("truncated") is False, "DOCUMENT_TREE_INVALID")
    entries = [v for v in tree["tree"] if v["type"] != "tree"]
    need(
        len(entries) <= 19 and all(v["mode"] == "100644" and v["type"] == "blob" for v in entries),
        "DOCUMENT_FILES_INVALID",
    )
    files = {v["path"]: destination.blob(v["sha"], v["size"]) for v in entries}
    need(path + "receipt.json" in files, "DOCUMENT_RECEIPT_MISSING")
    receipt = b.decode(files[path + "receipt.json"])
    count = receipt["part_count"]
    need(type(count) is int and 1 <= count <= 16, "DOCUMENT_PARTS_INVALID")
    names = [path + f"part-{i:03}.bin" for i in range(count)]
    need(set(files) == {path + "receipt.json", *names}, "DOCUMENT_FILES_INVALID")
    body = b"".join(files[n] for n in names)
    budget = document_ref(doc, True)
    held, parent = b.read(destination, budget, {path + "reservation.json"})
    need(
        parent == m.BOOTSTRAP and git["parents"][0]["sha"] == destination.ref(budget),
        "DOCUMENT_RESERVATION_INVALID",
    )
    need(
        held[path + "reservation.json"] == reservation(doc, receipt["source_commit"]),
        "DOCUMENT_RESERVATION_INVALID",
    )
    need(
        receipt["document"] == doc
        and receipt["plan_sha256"] == PLAN_HASH
        and receipt["body_sha256"] == m.sha256(body)
        and receipt["body_bytes"] == len(body)
        and receipt["policy"] == POLICY,
        "DOCUMENT_HASH_MISMATCH",
    )
    need(len(body) <= DOC_LIMIT, "DOCUMENT_TOO_LARGE")
    b.secret_free(body, (destination.token,))
    return {"body": body, "receipt": receipt, "commit": commit}


def capture_documents(destination, source, verify_only=False, fetch=fetch_document):
    results, made = {}, 0
    for doc in DOCUMENTS:
        path = DOCUMENT_PREFIX + doc["id"] + "/"
        if destination.ref(document_ref(doc)) is None:
            need(not verify_only, "DOCUMENT_MISSING")
            need(
                destination.ref(document_ref(doc, True)) is None,
                "UNKNOWN_DOCUMENT_REQUEST_NOT_RESET",
            )
            budget = b.create(
                destination,
                document_ref(doc, True),
                {path + "reservation.json": reservation(doc, source)},
                m.BOOTSTRAP,
            )
            status, body = fetch(doc)
            need(
                type(status) is int and 100 <= status <= 599 and 0 < len(body) <= DOC_LIMIT,
                "DOCUMENT_FETCH_INVALID",
            )
            parts = [body[i : i + m.FILE_LIMIT] for i in range(0, len(body), m.FILE_LIMIT)]
            receipt = {
                "schema": "real_etf_issuer_receipt_v1",
                "source_commit": source,
                "document": doc,
                "plan_sha256": PLAN_HASH,
                "http_status": status,
                "fetched_at": datetime.now(UTC).isoformat(),
                "body_sha256": m.sha256(body),
                "body_bytes": len(body),
                "part_count": len(parts),
                "policy": POLICY,
            }
            files = {path + f"part-{i:03}.bin": part for i, part in enumerate(parts)}
            files[path + "receipt.json"] = m.canonical(receipt)
            b.create(destination, document_ref(doc), files, budget)
            made += 1
        results[doc["id"]] = read_document(destination, doc)
    return results, made


def bar_rows(item, ticker, adjusted):
    value = b.decode(item["body"])
    need(
        value.get("ticker") == ticker
        and value.get("adjusted") is adjusted
        and not value.get("next_url"),
        "BAR_IDENTITY_INVALID",
    )
    rows = value.get("results")
    need(type(rows) is list and len(rows) >= 2, "BARS_MISSING")
    result = {}
    for row in rows:
        need(type(row.get("t")) is int, "BAR_CLOCK_INVALID")
        at = datetime.fromtimestamp(row["t"] / 1000, UTC).astimezone(NY)
        day = at.date()
        need(
            at.hour == at.minute == at.second == 0
            and date(2024, 9, 16) <= day <= date(2024, 9, 27),
            "BAR_WINDOW_INVALID",
        )
        need(day not in result, "BAR_DUPLICATE")
        result[day] = number(row["c"], positive=True)
    need(list(result) == sorted(result), "BAR_ORDER_INVALID")
    return result


def dividend_rows(item, ticker):
    value = b.decode(item["body"])
    need(not value.get("next_url") and type(value.get("results")) is list, "DIVIDENDS_MISSING")
    rows = []
    for row in value["results"]:
        need(
            row.get("ticker") == ticker and row.get("currency") == "USD",
            "DIVIDEND_IDENTITY_INVALID",
        )
        ex = date.fromisoformat(row["ex_dividend_date"])
        record = date.fromisoformat(row["record_date"])
        pay = date.fromisoformat(row["pay_date"])
        need(
            date(2024, 9, 16) <= ex <= date(2024, 9, 27) and ex <= pay and record <= pay,
            "DIVIDEND_DATES_INVALID",
        )
        rows.append(
            {
                "ex": ex,
                "record": record,
                "pay": pay,
                "amount": number(row["cash_amount"]),
                "vendor_type": row.get("distribution_type"),
            }
        )
    need(rows and len({v["ex"] for v in rows}) == len(rows), "DIVIDEND_SCOPE_INVALID")
    return rows


def return_probe(raw, adjusted, dividends):
    need(set(raw) == set(adjusted), "BAR_TIMELINE_CONFLICT")
    days = sorted(raw)
    cash = {v["ex"]: v["amount"] for v in dividends}
    need(set(cash).issubset(raw) and days[0] not in cash, "EX_BOUNDARY_MISSING")
    scale = adjusted[days[0]] / raw[days[0]]
    for day in days:
        need(
            abs(adjusted[day] - raw[day] * scale) <= Decimal("0.0002"), "SPLIT_ANCHOR_NOT_CONSTANT"
        )
    with localcontext() as ctx:
        ctx.prec = 40
        index = Decimal(1)
        units = Decimal(1) / raw[days[0]]
        price_index = Decimal(1)
        journal = []
        maximum = Decimal(0)
        for previous, day in zip(days, days[1:]):
            distribution = cash.get(day, Decimal(0))
            index *= (raw[day] + distribution) / raw[previous]
            price_index *= raw[day] / raw[previous]
            # Independent holding-unit accounting, not the index recurrence.
            receivable_income = units * distribution
            units += receivable_income / raw[day]
            unit_nav = units * raw[day]
            error = abs(index - unit_nav)
            maximum = max(maximum, error)
            need(error <= Decimal("1E-28"), "TOTAL_RETURN_ORACLE_MISMATCH")
            alternative = (adjusted[day] + distribution * scale) / adjusted[previous]
            need(
                abs(alternative - (raw[day] + distribution) / raw[previous]) <= Decimal("0.00001"),
                "ADJUSTED_DIVIDEND_UNIT_MISMATCH",
            )
            journal.append(
                {
                    "date": day.isoformat(),
                    "gross_index": str(index),
                    "unit_nav": str(unit_nav),
                    "price_only_index": str(price_index),
                    "absolute_error": str(error),
                    "source_raw_close": str(raw[day]),
                    "gross_per_original_share": str(distribution),
                }
            )
        need(index > price_index, "SPLIT_ONLY_MISLABELLED_TOTAL_RETURN")
        return {
            "status": "RETROSPECTIVE_GROSS_SIGNAL_MATH_PASS",
            "checkpoints": len(journal),
            "journal": journal,
            "max_error": str(maximum),
            "tolerance": "1E-28",
            "split_only_is_total_return": False,
            "cash_credit_assumed": False,
            "real_broker_nav_reconciled": False,
            "reinvestment_hypothesis": "gross income reinvested at ex-date close; not actual broker DRIP",
            "signal_added_to_cash_ledger": False,
            "historical_PIT_verified": False,
        }


def report(captured, documents, source):
    docs = {}
    for doc_id, item in documents.items():
        entry = {
            "receipt": item["receipt"],
            "archive_commit": item["commit"],
            "historical_known_at": None,
        }
        if item["receipt"]["http_status"] == 200 and item["body"].startswith(b"PK"):
            try:
                entry["workbook_schema"] = schema_summary(workbook(item["body"]))
                entry["structure"] = "WORKBOOK_READABLE"
            except (Blocked, ValueError, KeyError, ET.ParseError, zipfile.BadZipFile):
                entry["structure"] = "WORKBOOK_UNVERIFIED"
        else:
            entry["structure"] = "HTTP_OR_FORMAT_UNVERIFIED"
        docs[doc_id] = entry
    cases = {}
    for group in ("etf-1", "etf-2", "etf-3", "etf-4"):
        for ticker in p.GROUPS[group]:
            key = group + "-" + ticker.lower()
            raw = bar_rows(captured[key + "-raw"], ticker, False)
            adjusted = bar_rows(captured[key + "-split-adjusted"], ticker, True)
            dividends = dividend_rows(captured[key + "-dividends"], ticker)
            cases[ticker] = {
                "signal": return_probe(raw, adjusted, dividends),
                "classification": "ISSUER_CLASSIFICATION_MAPPING_UNVERIFIED",
                "unknown_distribution_cash_admission": "REJECTED",
                "ex_record_pay": [
                    {
                        "ex": v["ex"].isoformat(),
                        "record": v["record"].isoformat(),
                        "pay": v["pay"].isoformat(),
                    }
                    for v in dividends
                ],
            }
    return {
        "schema": "real_etf_probe_v1",
        "operator_commit": source,
        "plan_sha256": PLAN_HASH,
        "input_commits": INPUT_COMMITS,
        "input_receipts": {k: v["receipt"] for k, v in captured.items()},
        "issuer_documents": docs,
        "cases": cases,
        "policy": POLICY,
        "full_real_scenarios_accepted": 0,
        "historical_PIT_verified": False,
        "real_cash_ledger_reconciled": False,
        "strategy_performance_computed": False,
    }


def public_summary(value):
    return {
        "status": "REAL_ETF_RETROSPECTIVE_PROBE_COMPLETE_NOT_CERTIFIED",
        "plan_sha256": PLAN_HASH,
        "report_sha256": m.sha256(m.canonical(value)),
        "vendor_requests": 0,
        "issuer_documents": {
            k: {
                "http_status": v["receipt"]["http_status"],
                "structure": v["structure"],
                "schema": v.get("workbook_schema", []),
            }
            for k, v in value["issuer_documents"].items()
        },
        "cases": {
            k: {
                "signal": v["signal"]["status"],
                "checkpoints": v["signal"]["checkpoints"],
                "max_signal_oracle_error": v["signal"]["max_error"],
                "classification": v["classification"],
            }
            for k, v in value["cases"].items()
        },
        "full_real_scenarios_accepted": 0,
        "certification": "UNVERIFIED",
        **b.FALSE_FLAGS,
    }


def execute(destination, source, verify_only=False, fetch=fetch_document):
    destination.visibility(True)
    documents, made = capture_documents(destination, source, verify_only, fetch)
    value = report(inputs(destination), documents, source)
    summary = public_summary(value)
    path, ref = PREFIX + source + "/", "research-data/real-etf-probe-v1-" + source
    files = {path + "report.json": m.canonical(value), path + "summary.json": m.canonical(summary)}
    if destination.ref(ref) is None:
        need(not verify_only, "REPORT_MISSING")
        b.create(destination, ref, files, m.BOOTSTRAP)
    saved, parent = b.read(destination, ref, set(files))
    need(parent == m.BOOTSTRAP and saved == files, "REPORT_READBACK_MISMATCH")
    destination.visibility(True)
    return {**summary, "official_requests": made}


def guarded():
    source = m.sha(os.environ.get("REAL_ETF_PROBE_REVIEWED_SHA", ""))
    need(
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
        need(sys.argv[1:] in [["probe"], ["verify"]], "INVALID_COMMAND")
        source = guarded()
        verify = sys.argv[1] == "verify"
        destination = m.GitHub(
            m.DESTINATION, os.environ.get("ARCHIVE_WRITE_TOKEN", ""), writable=not verify
        )
        print(json.dumps(execute(destination, source, verify), separators=(",", ":")))
        return 0
    except Exception as error:
        code = str(error) if isinstance(error, Blocked) else "CHECK_FAILED"
        if not re.fullmatch(r"[A-Z_]{1,80}", code):
            code = "CHECK_FAILED"
        print(
            json.dumps(
                {"status": "REAL_ETF_PROBE_STOPPED", "code": code, "certification": "UNVERIFIED"},
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
