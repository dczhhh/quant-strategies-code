"""Bounded four-year input inventory, never acquisition or Engine admission.

Accepted normalized payloads prove represented rows. Their retained vendor URLs
prove requested scope, not that every requested row exists. In particular, a
requested-but-unrepresented row is an anomaly, never permission to retry.
"""

from __future__ import annotations

import gzip
import json
import re
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

from tools import bear_2022_capture as a
from tools import rotation_quantity_exit_audit as q

c, s, u = q.c, q.s, q.u
FIRST, LAST = date(2022, 1, 3), date(2025, 12, 31)
SOURCE = "6c6e8e36799c788111c86b157d652a56ea7ec5c3"
PREFIX = "research/verification/rotation_four_year_inventory_v1/"
REPORT_REF = "research-data/rotation-four-year-inventory-v1"
INPUT_PINS = {
    "bear2022": (
        "504cf70f26aa54e163cdc53aec2fb31b9615984b",
        "362348c8a126856c065452859ab2ce5fdff95c2823ee7a20f631006ef075ae6e",
    ),
    "primary": (
        "b57246b0c5792da90834f1ebb20a69be4d521a9e",
        "c1469175c1a2cc430adaa188525425c41b4dbb1588015739802295e911dd2ade",
    ),
    "secondary": (
        "a1b3d0980eada9d571b3a10d4d53b1b836c22abb",
        "81995254736506640bc1cb8f11fefafb207d336073d4531aafc10616d035a05e",
    ),
}
SPLIT_PINS = {
    "SPY": "83b526a45209cbe5a2fe47a8bb7d98d5ad0d5e34",
    "XLB": "69167f598f53a5b6b958246e585b6edd0afc8947",
    "XLC": "c9fee9fb80158f6b9c9677f9a0ceaab6beeb4556",
    "XLE": "2e5ada98697c011dc2057a74385351f605ac923b",
    "XLF": "54767ea540aa34e26b73857b14188586edfd16fc",
    "XLI": "41593622d70cbded7d82333fe099417b69708ea2",
    "XLK": "0a2f900f2533929aa23ad45cbfd2d906f7ea15d0",
    "XLP": "1b2dbf5c5a97bd96003e856fd3c23dd714ef5c68",
    "XLRE": "9d11ff0e1dcf9775d8df044a0f9fd04d260e481b",
    "XLU": "174aedb62d257bcc2f84c699ec2cbb713184bdff",
    "XLV": "f0cdb6ea0af2570944da67db280fb534c58b804e",
    "XLY": "7b39d3c54b55f89549f2f63b69272032e469695c",
}
ISSUER_SPLIT_NOTICE = (
    "https://investors.statestreet.com/investor-news-events/press-releases/news-details/2025/"
    "State-Street-Investment-Management-Announces-Share-Splits-for-Five-Select-Sector-SPDR-ETFs/default.aspx"
)
POLICY = {
    "schema": "rotation_four_year_inventory_policy_v1",
    "execution_start": str(FIRST),
    "execution_end": str(LAST),
    "universe": list(s.TICKERS),
    "accepted_source": SOURCE,
    "input_pins": INPUT_PINS,
    "split_raw_pins": SPLIT_PINS,
    "scope": "three_accepted_normalized_inputs_and_their_registered_ETF_vendor_sources",
    "unrelated_private_archives_searched": False,
    "entire_private_repository_absence_certified": False,
    "requested_scope_is_complete_actual_row_coverage": False,
    "requested_but_unrepresented_may_be_retried": False,
    "new_vendor_requests": 0,
    "new_official_requests": 0,
    "engine_invoked": False,
    "acquisition_executor_present": False,
    "continuous_inputs_ready": False,
    "PIT_verified": False,
    "performance_admission": False,
}
POLICY_HASH = s.digest(u.canonical(POLICY))


def need(ok, code):
    if not ok:
        raise ValueError("FOUR_YEAR_INVENTORY_" + code)


def spans(days, required):
    result, previous = [], -2
    for index, day in enumerate(required):
        if day not in days:
            continue
        if index == previous + 1:
            result[-1]["end"] = str(day)
            result[-1]["sessions"] += 1
        else:
            result.append({"start": str(day), "end": str(day), "sessions": 1})
        previous = index
    return result


def load_inputs(archive, window):
    """Read only pinned normalized bytes; do not claim to revalidate all raw data."""
    need(window in INPUT_PINS, "WINDOW")
    archive.visibility(True)
    revision, fingerprint = INPUT_PINS[window]
    need(archive.ref(q.REPORT_REF + window) == revision, "INPUT_REF_PIN")
    commit = archive.request("GET", "/git/commits/" + revision)
    tree = archive.request("GET", "/git/trees/" + commit["tree"]["sha"] + "?recursive=1")
    need(tree.get("truncated") is False, "INPUT_TREE")
    rows = [r for r in tree["tree"] if r["type"] != "tree"]
    entries = {r["path"]: r for r in rows}
    prefix = q.PREFIX + window + "/"
    need(
        len(commit["parents"]) == 1
        and commit["parents"][0]["sha"] == c.m.BOOTSTRAP
        and len(entries) == len(rows)
        and 1 < len(rows) <= 3 * q.e.MAX_PARTS + 1
        and all(
            r["mode"] == "100644" and r["type"] == "blob" and p.startswith(prefix)
            for p, r in entries.items()
        ),
        "INPUT_ARCHIVE_SCOPE",
    )

    def body(path):
        need(path in entries, "INPUT_PART_MISSING")
        row = entries[path]
        raw = archive.blob(row["sha"], row["size"])
        need(c.m.git_blob(raw) == row["sha"], "INPUT_GIT_BLOB")
        return raw

    manifest = json.loads(body(prefix + "manifest.json"))
    need(
        manifest["schema"] == "rotation_quantity_exit_manifest_v1"
        and manifest["source_commit"] == SOURCE
        and manifest["window"] == window
        and manifest["prior_bear2022_bundle"] == q.b.ANCHOR
        and manifest["prior_frequency_bundle"] == q.FREQUENCY_PIN
        and set(manifest["logical_files"]) == q.LOGICAL,
        "INPUT_MANIFEST",
    )
    item = manifest["logical_files"]["inputs.json"]
    parts = item["parts"]
    need(
        0 < len(parts) <= q.e.MAX_PARTS
        and parts == [prefix + f"inputs.json.gz.part-{i:02}.bin" for i in range(len(parts))],
        "INPUT_PART_PATHS",
    )
    chunks = [body(path) for path in parts]
    need(
        all(s.digest(raw) == manifest["files"][path] for path, raw in zip(parts, chunks))
        and s.digest(b"".join(chunks)) == item["compressed_sha256"],
        "INPUT_COMPRESSED_HASH",
    )
    raw = q.w.f.d.g.bounded_gunzip(b"".join(chunks), q.e.MAX_RAW)
    need(
        s.digest(raw) == item["uncompressed_sha256"] == fingerprint
        and len(raw) == item["uncompressed_bytes"],
        "INPUT_LOGICAL_HASH",
    )
    return json.loads(raw)


def requested_product(source):
    """Decode only registered, credential-free Massive request ranges."""
    uri = urlsplit(source["url"])
    if uri.hostname != "api.massive.com":
        return None
    need(
        uri.scheme == "https" and uri.netloc == "api.massive.com" and not uri.fragment,
        "SOURCE_URL",
    )
    params = parse_qs(uri.query, strict_parsing=True)
    need(all(len(v) == 1 for v in params.values()), "SOURCE_QUERY")
    match = re.fullmatch(r"/v2/aggs/ticker/([A-Z]+)/range/1/(day|minute)/([^/]+)/([^/]+)", uri.path)
    if match:
        ticker, kind, first, last = match.groups()
        need(
            params == {"adjusted": ["false"], "sort": ["asc"], "limit": ["5000"]}, "RAW_SHARE_QUERY"
        )
        if kind == "minute":
            first, last = (
                datetime.fromtimestamp(int(v) / 1000, u.NY).date() for v in (first, last)
            )
        else:
            first, last = date.fromisoformat(first), date.fromisoformat(last)
            kind = "daily"
    else:
        match = re.fullmatch(r"/stocks/v1/(dividends|splits)", uri.path)
        if match is None:
            raise ValueError("FOUR_YEAR_INVENTORY_UNREGISTERED_VENDOR_ENDPOINT")
        kind = match[1]
        field = "ex_dividend_date" if kind == "dividends" else "execution_date"
        need(
            set(params) == {"ticker", field + ".gte", field + ".lte", "sort", "limit"}
            and params["sort"] == [field + ".asc"]
            and params["limit"] == ["100"],
            "ACTION_QUERY",
        )
        ticker = params["ticker"][0]
        first, last = (date.fromisoformat(params[field + v][0]) for v in (".gte", ".lte"))
    need(ticker in s.TICKERS and first <= last, "SOURCE_SCOPE")
    return {"ticker": ticker, "kind": kind, "first": first, "last": last}


def read_splits(archive, normalized):
    """Reopen the source archive and bind all four raw products to accepted hashes."""
    result = {}
    accepted = {v["identifier"]: v for v in normalized["sources"]}
    for ticker in s.TICKERS:
        need(archive.ref(a.ref(ticker, "raw")) == SPLIT_PINS[ticker], "SPLIT_REF_PIN")
        bodies = a.read_raw(archive, ticker)
        need(
            all(
                s.digest(raw) == accepted[ticker + ":source:" + name]["sha256"]
                for name, raw in bodies.items()
            ),
            "SPLIT_RAW_SOURCE_HASH",
        )
        source = accepted[ticker + ":source:massive_splits"]
        product = requested_product(source)
        need(
            product is not None and product["first"] <= FIRST <= LAST <= product["last"],
            "SPLIT_SCOPE",
        )
        events = s.massive(bodies["massive_splits"], ticker, "splits")
        result[ticker] = {
            "source": source,
            "archive_commit": SPLIT_PINS[ticker],
            "events": [
                {"session": str(day), "new_per_old": str(ratio)}
                for day, ratio in events.items()
                if FIRST <= day <= LAST
            ],
        }
    return result


def product(ticker, kind, first, last):
    need(ticker in s.TICKERS and kind in {"daily", "minute", "dividends"}, "PLAN_SCOPE")
    need(FIRST <= first <= last <= LAST, "PLAN_DATES")
    if kind in {"daily", "minute"}:
        lo, hi = str(first), str(last)
        if kind == "minute":
            lo = str(int(c.CAL.session(first).market_open.timestamp() * 1000))
            hi = str(int(c.CAL.session(last).market_close.timestamp() * 1000) - 1)
        endpoint = (
            f"v2/aggs/ticker/{ticker}/range/1/{'day' if kind == 'daily' else 'minute'}/{lo}/{hi}"
        )
        query = "?adjusted=false&sort=asc&limit=5000"
    else:
        endpoint = "stocks/v1/dividends"
        query = f"?ticker={ticker}&ex_dividend_date.gte={first}&ex_dividend_date.lte={last}&sort=ex_dividend_date.asc&limit=100"
    return {
        "id": f"{ticker.lower()}-{kind}-{first}-{last}",
        "ticker": ticker,
        "kind": kind,
        "start": str(first),
        "end": str(last),
        "url": "https://api.massive.com/" + endpoint + query,
    }


def registered_products():
    """Review the frozen ETF acquisition families, including failed planned slots.

    A recovery replaces a failed slot in one of these same plans, not its scope.
    Monthly Yahoo tail observations and adjusted views cannot supply daily raw
    prices or minute fills. Unrelated account/stock archives are not scanned.
    """
    from tools import bear_2022_market, real_action_capture, real_etf_2025, rotation_stress_capture

    families = {
        "bear_2022_source": [p for t in s.TICKERS for p in s.products(t)],
        "bear_2022_market": [p for t in s.TICKERS for p in bear_2022_market.products(t)],
        "rotation_2025": [p for t in s.TICKERS for p in c.products(t)],
        "rotation_stress": [p for t in s.TICKERS for p in rotation_stress_capture.products(t)],
        "real_etf_2025": [p for t in s.TICKERS for p in real_etf_2025.products(t)],
        "real_actions_2024": [
            p
            for group in real_action_capture.GROUPS
            for p in real_action_capture.products(group)
            if p["ticker"] in s.TICKERS
        ],
    }
    result = []
    for family, products in families.items():
        for row in products:
            if row.get("adjusted") is True:
                continue
            spec = requested_product({"url": row["url"]})
            if spec:
                result.append({"family": family, "url": row["url"], **spec})
    return result


def analyze(inputs, splits, *, first=FIRST, last=LAST):
    """Separate actual rows, requested scope, missing scope and annual evidence."""
    need(set(inputs) == set(INPUT_PINS) and set(splits) == set(s.TICKERS), "INPUT_SET")
    required = c.sessions(first, last)
    required_set = set(required)
    closes, bars, requested, represented_times = {}, {}, {}, {}
    distributions, windows, sources = {}, {}, {}
    registry = registered_products()
    planned = {}
    for spec in registry:
        planned.setdefault((spec["ticker"], spec["kind"]), set()).update(
            d for d in required if spec["first"] <= d <= spec["last"]
        )
    for window in INPUT_PINS:
        value = inputs[window]
        source_map = {row["identifier"]: row for row in value["sources"]}
        need(len(source_map) == len(value["sources"]), "DUPLICATE_SOURCE")
        for row in value["sources"]:
            spec = requested_product(row)
            if spec:
                key = (row["url"], row["sha256"])
                sources[key] = row
                requested.setdefault((spec["ticker"], spec["kind"]), set()).update(
                    d for d in required if spec["first"] <= d <= spec["last"]
                )
        execution_days = set()
        for kind, rows, target in (
            ("daily", value["closes"], closes),
            ("minute", value["bars"], bars),
        ):
            seen = set()
            for row in rows:
                asset = row["asset"]
                if kind == "daily":
                    stamp = day = date.fromisoformat(row["session"])
                else:
                    stamp = datetime.fromisoformat(row["end"])
                    need(stamp.utcoffset() is not None, "ROW_CLOCK")
                    day = stamp.astimezone(u.NY).date()
                need(asset in s.TICKERS and row["source_id"] in source_map, "ROW_SOURCE")
                key = (asset, stamp)
                need(key not in seen, "DUPLICATE_ROW")
                seen.add(key)
                if day not in required_set:
                    continue
                economic = (
                    (Decimal(row["close"]),)
                    if kind == "daily"
                    else tuple(Decimal(row[k]) for k in ("open", "high", "low", "close", "volume"))
                )
                need(
                    all(v.is_finite() and v > 0 for v in economic[:4])
                    and (kind == "daily" or economic[-1].is_finite() and economic[-1] >= 0),
                    "ROW_NUMBER",
                )
                need(key not in target or target[key] == economic, "OVERLAP_ECONOMIC_CONFLICT")
                target[key] = economic
                if kind == "minute":
                    execution_days.add(day)
                    represented_times.setdefault((asset, day), set()).add(stamp)
        for event in value["distributions"]:
            key = (event["asset"], event["event_id"])
            economic = tuple(event[k] for k in ("ex_date", "record_date", "pay_date")) + (
                Decimal(event["cash_amount"]),
            )
            need(
                key not in distributions or distributions[key] == economic, "EVENT_OVERLAP_CONFLICT"
            )
            distributions[key] = economic
        windows[window] = {
            "normalized_sha256": INPUT_PINS[window][1],
            "execution_sessions": len(execution_days),
            "settlement_reference_first": value["settlement_sessions"][0],
            "settlement_reference_last": value["settlement_sessions"][-1],
            "settlement_reference_source": source_map[value["settlement_source_id"]],
            "classifications": dict(
                sorted(
                    Counter(
                        f"{event['ex_date'][:4]}:{event['classification']}"
                        for event in value["distributions"]
                    ).items()
                )
            ),
        }
    per_asset, proposed = {}, []
    common_minute, common_daily = set(required), set(required)
    for ticker in s.TICKERS:
        daily = {d for asset, d in closes if asset == ticker}
        complete, partial = set(), []
        for day in required:
            session = c.CAL.session(day)
            times = {session.market_open + timedelta(minutes=m) for m in (1, *range(5, 16))} | {
                session.market_close
            }
            represented = represented_times.get((ticker, day), set())
            if times <= represented:
                complete.add(day)
            elif represented:
                partial.append(str(day))
        common_minute &= complete
        common_daily &= daily
        row = {
            "daily_sessions": len(daily),
            "complete_execution_sessions": len(complete),
            "partial_execution_sessions": partial,
        }
        for kind, actual in (("daily", daily), ("minute", complete), ("dividends", set())):
            scope = requested.get((ticker, kind), set())
            missing = required_set - (scope if kind == "dividends" else actual)
            eligible = missing - scope - planned.get((ticker, kind), set())
            anomaly = missing & scope if kind != "dividends" else set()
            row[kind + "_missing_spans"] = spans(missing, required)
            row[kind + "_requested_but_unrepresented_spans"] = spans(anomaly, required)
            row[kind + "_withheld_registered_plan_spans"] = spans(
                missing & planned.get((ticker, kind), set()) - scope, required
            )
            for span in spans(eligible, required):
                days = c.sessions(
                    date.fromisoformat(span["start"]), date.fromisoformat(span["end"])
                )
                size = 3 if kind == "minute" else len(days)
                proposed += [
                    product(ticker, kind, days[i], days[min(i + size - 1, len(days) - 1)])
                    for i in range(0, len(days), size)
                ]
        need(
            requested.get((ticker, "splits"), set()) == required_set,
            "SPLIT_REQUEST_SCOPE_INCOMPLETE",
        )
        per_asset[ticker] = row
    counts = dict(sorted(Counter(p["kind"] for p in proposed).items()))
    split_events = [dict(asset=t, **event) for t in s.TICKERS for event in splits[t]["events"]]
    summary = {
        "expected_execution_sessions": len(required),
        "accepted_complete_execution_sessions": len(common_minute),
        "missing_execution_sessions": len(required_set - common_minute),
        "missing_execution_spans": spans(required_set - common_minute, required),
        "accepted_daily_sessions": len(common_daily),
        "missing_daily_sessions": len(required_set - common_daily),
        "missing_daily_spans": spans(required_set - common_daily, required),
        "proposed_registered_scope_products": counts,
        "proposed_registered_scope_requests": len(proposed),
        "new_split_requests_needed_for_registered_scope": 0,
        "retained_in_window_split_events": split_events,
        "any_requested_but_unrepresented_rows": any(
            v[k + "_requested_but_unrepresented_spans"]
            for v in per_asset.values()
            for k in ("daily", "minute")
        ),
        "new_vendor_requests": 0,
        "new_official_requests": 0,
        "engine_invoked": False,
        "continuous_inputs_ready": False,
        "PIT_verified": False,
        "performance_admission": False,
    }
    return {
        "schema": "rotation_four_year_inventory_v1",
        "policy": POLICY,
        "policy_sha256": POLICY_HASH,
        "summary": summary,
        "accepted_windows": windows,
        "per_asset": per_asset,
        "registered_sources": sorted(sources.values(), key=lambda v: (v["url"], v["sha256"])),
        "registered_acquisition_plan_review": registry,
        "reopened_split_sources": splits,
        "proposed_products_not_executed": proposed,
        "outstanding_admission_work": [
            "Check create-only reservations before any capture; reviewed plan scope is not an entire-repository absence certificate.",
            "Retain same-year 2023/2024 distribution classification; old 2024 short-window assumption is not extended.",
            "Verify every 2025 event including year-end against issuer original-share cash and same-year classification rows.",
            "Freeze continuous settlement reference through final T+1 obligation, including historical 2024-05-28 transition and bank holidays.",
            "Exercise five 2025-12-05 splits through holdings, causal total return, pending orders, fees and independent Decimal ledger.",
            "Preserve year-end dividend receivables payable in 2026 without invented release or forced liquidation.",
            "Freeze new five-group frequency matrix, SPY controls and one continuous account; no annual reset or window splicing.",
        ],
        "issuer_split_notice_reference_not_archived_by_this_inventory": ISSUER_SPLIT_NOTICE,
    }


def build(factory):
    inputs = {window: load_inputs(factory(), window) for window in INPUT_PINS}
    splits = read_splits(factory(), inputs["bear2022"])
    return analyze(inputs, splits)


def payload(report, source):
    c.m.sha(source)
    raw = u.canonical(report)
    packed = gzip.compress(raw, mtime=0)
    need(len(packed) <= c.m.FILE_LIMIT, "REPORT_BUDGET")
    path = PREFIX + "inventory.json.gz"
    return {
        path: packed,
        PREFIX + "manifest.json": u.canonical(
            {
                "schema": "rotation_four_year_inventory_manifest_v1",
                "source_commit": source,
                "policy_sha256": POLICY_HASH,
                "uncompressed_sha256": s.digest(raw),
                "files": {path: s.digest(packed)},
            }
        ),
    }


def retain(archive, files, *, verify_only):
    archive.visibility(True)
    need(set(files) == {PREFIX + "inventory.json.gz", PREFIX + "manifest.json"}, "REPORT_PATHS")
    need(all(0 < len(v) <= c.m.FILE_LIMIT for v in files.values()), "REPORT_BUDGET")
    if archive.ref(REPORT_REF) is None:
        need(not verify_only, "REPORT_MISSING")
        c.b.create(archive, REPORT_REF, files, c.m.BOOTSTRAP)
    saved, parent = c.b.read(archive, REPORT_REF, set(files))
    need(parent == c.m.BOOTSTRAP and saved == files, "REPORT_BYTES")
    return s.digest(u.canonical({k: s.digest(v) for k, v in sorted(files.items())}))
