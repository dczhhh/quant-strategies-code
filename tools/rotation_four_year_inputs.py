"""Rebuild all raw-backed inputs into one continuous, explicitly retrospective run.

This is an opt-in builder, not an Engine invocation or source-finality claim.
"""

from __future__ import annotations

import subprocess
from collections import Counter
from dataclasses import asdict, replace
from datetime import date, datetime
from decimal import Decimal

from tools import bear_2022_capture as raw_2022
from tools import rotation_four_year_annual as annual
from tools import rotation_four_year_settlement as settlement

a, c, u = annual.a, annual.c, annual.u
i, g, q = a.i, a.g, a.i.q
FIRST, LAST = i.FIRST, i.LAST


def need(ok, code):
    if not ok:
        raise ValueError("FOUR_YEAR_INPUTS_" + code)


def old_inputs(factory):
    """No normalized-only shortcut: existing builders re-open the original bytes."""
    expected = {window: i.load_inputs(factory(), window) for window in i.INPUT_PINS}
    bear, body, _ = q.e.x.build_inputs(factory, q.b.BASE)
    need(body == u.canonical(expected["bear2022"]), "BEAR_INPUT_BYTES")
    primary, body, _ = q.w.f.x.build_inputs(
        factory, q.w.f.d.BASELINE_SOURCE, u.pa.ClosePriceAudit()
    )
    need(body == u.canonical(expected["primary"]), "PRIMARY_INPUT_BYTES")
    docs = q.w.f.x.s.documents(factory())[0]
    secondary, body = u.build_inputs(
        factory, q.w.f.d.BASELINE_SOURCE, docs, audit=u.pa.ClosePriceAudit()
    )
    need(body == u.canonical(expected["secondary"]), "SECONDARY_INPUT_BYTES")
    return {"bear2022": bear, "primary": primary, "secondary": secondary}, expected


class Rows:
    """Deterministic source namespacing and exact Decimal overlap comparison."""

    def __init__(self):
        self.sources, self.closes, self.bars = {}, {}, {}
        self.distributions, self.splits, self.securities = {}, {}, {}
        self.overlaps = Counter()

    def source(self, value):
        need(
            value.identifier not in self.sources or self.sources[value.identifier] == value,
            "SOURCE_ID_COLLISION",
        )
        self.sources[value.identifier] = value

    def price(self, kind, row):
        target = self.closes if kind == "closes" else self.bars
        key = (row.asset, row.session if kind == "closes" else row.end)
        fields = ("close",) if kind == "closes" else ("open", "high", "low", "close", "volume")
        if key in target:
            need(
                all(
                    Decimal(getattr(target[key], name)) == Decimal(getattr(row, name))
                    for name in fields
                ),
                "PRICE_OVERLAP_CONFLICT",
            )
            self.overlaps[kind] += 1
        else:
            target[key] = row

    def distribution(self, event):
        key = (event.asset, event.ex_date)
        if key in self.distributions:
            prior = self.distributions[key]
            need(
                (prior.event_id, prior.record_date, prior.pay_date, Decimal(prior.cash_amount))
                == (event.event_id, event.record_date, event.pay_date, Decimal(event.cash_amount)),
                "DISTRIBUTION_OVERLAP_CONFLICT",
            )
            self.overlaps["distributions"] += 1
        else:
            self.distributions[key] = event

    def split(self, event):
        key = (event.asset, event.session)
        if key in self.splits:
            need(self.splits[key].new_per_old == event.new_per_old, "SPLIT_OVERLAP_CONFLICT")
            self.overlaps["splits"] += 1
        else:
            self.splits[key] = event

    def window(self, label, inputs):
        def identifier(sid):
            return label + ":" + sid

        for source in inputs.sources:
            self.source(replace(source, identifier=identifier(source.identifier)))
        for kind in ("closes", "bars"):
            for row in getattr(inputs, kind):
                self.price(kind, replace(row, source_id=identifier(row.source_id)))
        for row in inputs.distributions:
            self.distribution(
                replace(
                    row,
                    source_id=identifier(row.source_id),
                    classification_source_id=identifier(row.classification_source_id)
                    if row.classification_source_id is not None
                    else None,
                )
            )
        for row in inputs.splits:
            self.split(replace(row, source_id=identifier(row.source_id)))
        for row in inputs.securities:
            if row.asset in self.securities:
                prior = asdict(self.securities[row.asset])
                current = asdict(row)
                del prior["source_id"], current["source_id"]
                need(prior == current, "SECURITY_IDENTITY_CONFLICT")
            else:
                self.securities[row.asset] = replace(row, source_id=identifier(row.source_id))


def add_gaps(rows, factory):
    receipts = {}
    for ticker in g.TICKERS:
        held, made = g.capture(
            factory(),
            ticker,
            a.CAPTURE_SOURCE,
            lambda: need(False, "PROVIDER_CREDENTIAL_FORBIDDEN"),
            verify_only=True,
        )
        need(made == 0 and len(held) == 192, "CAPTURE_SCOPE")
        receipts[ticker] = []
        for spec in g.products(ticker):
            item = held[spec["id"]]
            sid = "four-year-gap:" + spec["id"]
            source = u.source(sid, item["body"], item["receipt"], spec["url"])
            rows.source(source)
            receipts[ticker].append(
                {
                    "product_id": spec["id"],
                    "capture_commit": item["commit"],
                    "body_sha256": source.sha256,
                }
            )
            if spec["kind"] in {"daily", "minute"}:
                for stamp, values, volume in u.bars(spec, item["body"]):
                    if spec["kind"] == "daily":
                        rows.price("closes", u.RawClose(ticker, stamp, values["c"], sid))
                    else:
                        rows.price(
                            "bars",
                            u.ResearchBar(
                                ticker,
                                stamp,
                                values["o"],
                                values["h"],
                                values["l"],
                                values["c"],
                                volume,
                                sid,
                            ),
                        )
            else:
                for event in u.actions(spec, item["body"]):
                    rows.distribution(
                        u.ResearchDistribution(
                            ticker,
                            event["id"],
                            event["ex"],
                            event["record"],
                            event["pay"],
                            event["amount"],
                            "unclassified_signal_only",
                            sid,
                            None,
                        )
                    )
    return receipts


def add_original_splits(rows, factory, bear_normalized):
    pins = i.read_splits(factory(), bear_normalized)
    for ticker in g.TICKERS:
        archive = factory()
        need(archive.ref(raw_2022.ref(ticker, "raw")) == i.SPLIT_PINS[ticker], "SPLIT_REF_PIN")
        body = raw_2022.read_raw(archive, ticker)["massive_splits"]
        sid = "bear2022:" + ticker + ":source:massive_splits"
        need(c.m.sha256(body) == rows.sources[sid].sha256, "SPLIT_BODY_HASH")
        spec = i.requested_product(pins[ticker]["source"])
        need(spec is not None and spec["kind"] == "splits", "SPLIT_PRODUCT")
        events = u.actions(
            {
                "ticker": ticker,
                "kind": "splits",
                "start": str(spec["first"]),
                "end": str(spec["last"]),
            },
            body,
        )
        for event in events:
            if FIRST <= event["date"] <= LAST:
                # Actual source identity, not an invented split-event identifier.
                rows.split(u.ResearchSplit(ticker, event["id"], event["date"], event["ratio"], sid))
    actual = {
        (e.asset, e.session, e.new_per_old)
        for e in rows.splits.values()
        if FIRST <= e.session <= LAST
    }
    need(
        actual
        == {
            (ticker, date(2025, 12, 5), Decimal(2))
            for ticker in ("XLB", "XLE", "XLK", "XLU", "XLY")
        },
        "FIVE_REAL_SPLITS",
    )
    return pins


def classify(rows, history, tax_2025, tax_by_year):
    """Match every native execution cash record, not just the new gap records."""
    earliest = min(d for _, d in rows.closes)
    expected = {
        (row["B"], c.p.issuer_day(row["D"]))
        for row in history["dividend"][1:]
        if row.get("B") in g.TICKERS and earliest <= c.p.issuer_day(row["D"]) <= LAST
    }
    need(set(rows.distributions) == expected, "ISSUER_NATIVE_EVENT_COVERAGE")
    checks, counts = [], Counter()
    for key, value in sorted(rows.distributions.items()):
        if value.ex_date < FIRST:
            need(value.classification == "unclassified_signal_only", "WARMUP_CLASSIFICATION")
            continue
        event = {
            "ex": value.ex_date,
            "record": value.record_date,
            "pay": value.pay_date,
            "amount": value.cash_amount,
        }
        a.issuer_match(history, value.asset, event)
        year = value.ex_date.year
        if year == 2022:
            need(
                value.classification == "assumed_ordinary_fund"
                and value.classification_source_id == "bear2022:assumption:2022",
                "ONLY_2022_ASSUMPTION",
            )
            evidence = {"status": "ACCEPTED_OWNER_ASSUMPTION_2022_ONLY"}
            final = value
        else:
            need(year in (2023, 2024, 2025), "CLASSIFICATION_YEAR")
            evidence = (
                a.ordinary_2025(history, tax_2025, value.asset, event)
                if year == 2025
                else annual.fields(year, history, tax_by_year[year], value.asset, event)
            )
            final = replace(
                value,
                classification="ordinary_fund",
                classification_source_id=f"four-year:tax:{year}",
            )
        rows.distributions[key] = final
        counts[f"{year}:{final.classification}"] += 1
        checks.append(
            {
                "asset": value.asset,
                "event_id": value.event_id,
                "ex_date": str(value.ex_date),
                "native_source_id": value.source_id,
                "tax_evidence": evidence,
            }
        )
    need(
        counts
        == Counter(
            {
                f"{year}:{'assumed_ordinary_fund' if year == 2022 else 'ordinary_fund'}": 48
                for year in range(2022, 2026)
            }
        ),
        "EXECUTION_EVENT_COUNTS",
    )
    return checks, dict(sorted(counts.items()))


def finish(rows, days, settlement_sid):
    warmup = q.e.x.s.WARMUP
    need(
        min(day for _, day in rows.closes) == warmup
        and len(rows.closes) == len(c.sessions(warmup, LAST)) * 12,
        "WARMUP_OR_CLOSE_COVERAGE",
    )
    bars = tuple(sorted(rows.bars.values(), key=lambda r: (r.end, r.asset)))
    need(
        len(bars) == 1003 * 13 * 12
        and bars[0].end.astimezone(u.NY).date() == FIRST
        and bars[-1].end.astimezone(u.NY).date() == LAST,
        "EXECUTION_BOUNDARIES",
    )
    used = {settlement_sid}
    for kind in ("closes", "bars", "distributions", "splits", "securities"):
        for row in getattr(rows, kind).values():
            used.add(row.source_id)
            if kind == "distributions" and row.classification_source_id is not None:
                used.add(row.classification_source_id)
    need(used <= set(rows.sources), "UNBOUND_SOURCE")
    # Old 2024 assumptions and window calendars are not retained as active sources.
    return u.ResearchInputs(
        tuple(rows.sources[sid] for sid in sorted(used)),
        tuple(sorted(rows.closes.values(), key=lambda r: (r.session, r.asset))),
        bars,
        tuple(sorted(rows.distributions.values(), key=lambda r: (r.ex_date, r.asset, r.event_id))),
        tuple(sorted(rows.splits.values(), key=lambda r: (r.session, r.asset, r.event_id))),
        tuple(rows.securities[key] for key in sorted(rows.securities)),
        days,
        settlement_sid,
        origin="retained_prices_with_assumed_distribution",
        close_policy=u.SEPARATE_CLOSES,
    )


def build(factory, source):
    c.m.sha(source)
    old, normalized = old_inputs(factory)
    rows = Rows()
    for window in i.INPUT_PINS:
        rows.window(window, old[window])
    docs = c.a.issuer_documents(factory())
    history = c.p.workbook(docs[c.p.DOCUMENTS[0]["id"]]["body"])
    tax_2025 = c.p.workbook(docs[c.p.DOCUMENTS[1]["id"]]["body"])
    archival = annual.read(factory())
    annual_audit = annual.audit(archival, history)
    tax_by_year = {year: c.p.workbook(item["body"]) for year, item in archival.items()}
    for year, item in archival.items():
        rows.source(replace(item["source"], identifier=f"four-year:tax:{year}"))
    doc = c.p.DOCUMENTS[1]
    rows.source(
        u.source(
            "four-year:tax:2025", docs[doc["id"]]["body"], docs[doc["id"]]["receipt"], doc["url"]
        )
    )
    receipts = add_gaps(rows, factory)
    split_pins = add_original_splits(rows, factory, normalized["bear2022"])
    cash_checks, counts = classify(rows, history, tax_2025, tax_by_year)
    settlement_report = settlement.audit()
    _, days, body = settlement.reference()
    clock = datetime.fromisoformat(
        subprocess.check_output(["git", "show", "-s", "--format=%cI", source], text=True).strip()
    )
    sid = "four-year:independent-settlement"
    rows.source(
        u.ResearchSource(
            sid,
            c.m.sha256(body),
            clock,
            f"https://github.com/{c.m.CODE}/blob/{source}/{settlement.PATH}",
        )
    )
    inputs = finish(rows, days, sid)
    normalized_body = u.canonical({"schema": inputs.bundle_schema, **asdict(inputs)})
    return (
        inputs,
        normalized_body,
        {
            "schema": "rotation_four_year_continuous_inputs_v1",
            "source_commit": source,
            "normalized_sha256": c.m.sha256(normalized_body),
            "old_input_pins": i.INPUT_PINS,
            "all_old_input_raw_sources_revalidated": True,
            "all_old_normalized_bytes_reproduced_exactly": True,
            "capture_source": a.CAPTURE_SOURCE,
            "capture_plan_sha256": g.PLAN_HASH,
            "capture_receipt_index": receipts,
            "exact_economic_overlap_counts": dict(sorted(rows.overlaps.items())),
            "execution_sessions": 1003,
            "native_execution_distributions": counts,
            "native_cash_and_tax_checks": cash_checks,
            "archival_same_year_issuer_event_counts": {
                str(year): sum(map(len, assets.values())) for year, assets in annual_audit.items()
            },
            "annual_source_provenance": annual.provenance(),
            "split_source_pins": split_pins,
            "settlement_reference_audit": settlement_report,
            "close_comparison": inputs.close_comparison,
            "one_continuous_account_inputs": True,
            "annual_reset_or_window_return_splicing": False,
            "only_assumed_distribution_years": [2022],
            "retrospective_model_inputs_ready": True,
            "strict_issuer_authenticity_or_PIT_admission": False,
            "new_vendor_requests": 0,
            "new_official_requests": 0,
            "engine_invoked": False,
            "PIT_verified": False,
            "performance_admission": False,
        },
    )
