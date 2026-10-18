"""Finite 2022 minute/action capture after price-source qualification; no Engine."""

from __future__ import annotations

import base64
import gzip
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import bear_2022_capture as a  # noqa: E402

s, c = a.s, a.c
PREFIX = "research/data/bear_2022_market_v1/"
GROUP_SIZE, MAX_PARTS = 21, 32
SOURCE_READER = "94259b7687f453fbefa96a32d1f3a78506c6f9bf"
WORKFLOW = ".github/workflows/bear-2022-market.yml"
POLICY = {
    "schema": "bear_2022_market_capture_v1",
    "universe": list(s.TICKERS),
    "execution_start": str(s.FIRST),
    "execution_end": str(s.LAST),
    "dividend_start": str(s.WARMUP),
    "split_end": str(s.OVERLAP - timedelta(days=1)),
    "source_reader_sha": SOURCE_READER,
    "source_plan_sha256": s.PLAN_HASH,
    "maximum_market_requests": 1032,
    "maximum_requests_per_asset": 86,
    "maximum_requests_per_group": GROUP_SIZE,
    "maximum_response_bytes": s.MAX_RESPONSE_BYTES,
    "maximum_archive_parts_per_group": MAX_PARTS,
    "original_share_prices_and_cash": True,
    "new_daily_aggregate_requests": 0,
    "automatic_retry_allowed": False,
    "redirects_allowed": False,
    "pagination_allowed": False,
    "unknown_reservation_reset_allowed": False,
    "engine_invoked": False,
    "PIT_verified": False,
    "performance_admission": False,
}


def products(ticker):
    s.need(ticker in s.TICKERS, "MARKET_TICKER_SCOPE")
    days = c.sessions(s.FIRST, s.LAST)
    s.need(len(days) == 251 and days[0] == s.FIRST and days[-1] == s.LAST, "MARKET_CALENDAR")
    rows = []
    for kind, field, last in (
        ("dividends", "ex_dividend_date", s.LAST),
        ("splits", "execution_date", s.OVERLAP - timedelta(days=1)),
    ):
        rows.append(
            {
                "id": kind,
                "ticker": ticker,
                "kind": kind,
                "start": str(s.WARMUP),
                "end": str(last),
                "url": f"https://api.massive.com/stocks/v1/{kind}?ticker={ticker}"
                f"&{field}.gte={s.WARMUP}&{field}.lte={last}&sort={field}.asc&limit=100",
            }
        )
    for index in range(0, len(days), 3):
        chunk = days[index : index + 3]
        start = int(c.CAL.session(chunk[0]).market_open.timestamp() * 1000)
        end = int(c.CAL.session(chunk[-1]).market_close.timestamp() * 1000) - 1
        rows.append(
            {
                "id": "minute-" + str(chunk[0]),
                "ticker": ticker,
                "kind": "minute",
                "start": str(chunk[0]),
                "end": str(chunk[-1]),
                "url": f"https://api.massive.com/v2/aggs/ticker/{ticker}/range/1/minute/"
                f"{start}/{end}?adjusted=false&sort=asc&limit=5000",
            }
        )
    s.need(len(rows) == 86, "MARKET_REQUEST_BUDGET")
    return tuple(rows)


PLAN_HASH = s.digest(s.canonical({"policy": POLICY, "products": {t: products(t) for t in s.TICKERS}}))


def groups(ticker):
    rows = products(ticker)
    return tuple(rows[i : i + GROUP_SIZE] for i in range(0, len(rows), GROUP_SIZE))


def ref(ticker, index, reservation=False):
    s.need(type(index) is int and 0 <= index < len(groups(ticker)), "MARKET_GROUP_SCOPE")
    prefix = "research-reservations/" if reservation else "research-data/"
    return prefix + f"bear-2022-market-v1-{ticker.lower()}-{index}"


def hold_bytes(ticker, index, source):
    return s.canonical(
        {
            "source_commit": c.m.sha(source),
            "plan_sha256": PLAN_HASH,
            "policy": POLICY,
            "products": groups(ticker)[index],
        }
    )


def fetch(product, key):
    s.need(product in products(product["ticker"]), "MARKET_REQUEST_SCOPE")
    s.need(
        type(key) is str and 0 < len(key) <= 4096 and not any(ch.isspace() for ch in key),
        "MARKET_CREDENTIAL_UNAVAILABLE",
    )
    request = Request(product["url"], headers={"Authorization": "Bearer " + key})
    try:
        with build_opener(c.m.NoRedirect()).open(request, timeout=45) as response:
            return response.status, response.read(c.m.FILE_LIMIT + 1)
    except HTTPError as error:
        with error:
            return error.code, error.read(c.m.FILE_LIMIT + 1)


def read_group(archive, ticker, index):
    prefix = PREFIX + f"{ticker.lower()}/{index}/"
    revision = archive.ref(ref(ticker, index))
    s.need(revision is not None, "MARKET_RAW_MISSING")
    commit = archive.request("GET", "/git/commits/" + c.m.sha(revision))
    tree = archive.request("GET", "/git/trees/" + commit["tree"]["sha"] + "?recursive=1")
    rows = [row for row in tree["tree"] if row["type"] != "tree"]
    s.need(
        tree.get("truncated") is False
        and 2 <= len(rows) <= MAX_PARTS + 1
        and len({row["path"] for row in rows}) == len(rows)
        and all(row["type"] == "blob" and row["mode"] == "100644" for row in rows),
        "MARKET_RAW_TREE",
    )
    files = {row["path"]: archive.blob(row["sha"], row["size"]) for row in rows}
    manifest = c.b.decode(files[prefix + "manifest.json"])
    count = manifest["part_count"]
    s.need(
        type(count) is int
        and 1 <= count <= MAX_PARTS
        and set(files)
        == {prefix + "manifest.json", *(prefix + f"part-{i:02}.bin" for i in range(count))},
        "MARKET_RAW_PARTS",
    )
    held, parent = c.b.read(archive, ref(ticker, index, True), {prefix + "reservation.json"})
    packed = b"".join(files[prefix + f"part-{i:02}.bin"] for i in range(count))
    s.need(
        parent == c.m.BOOTSTRAP
        and len(commit["parents"]) == 1
        and commit["parents"][0]["sha"] == archive.ref(ref(ticker, index, True))
        and held[prefix + "reservation.json"] == hold_bytes(ticker, index, manifest["source_commit"])
        and manifest["bundle_sha256"] == s.digest(packed),
        "MARKET_RAW_BINDING",
    )
    decoded = a.g.bounded_gunzip(packed, GROUP_SIZE * (c.m.FILE_LIMIT * 2 + 8192))
    records = c.b.decode(decoded)["rows"]
    plan = groups(ticker)[index]
    s.need(
        len(records) <= len(plan) and manifest["recorded_products"] == len(records),
        "MARKET_RAW_RECORDS",
    )
    s.need(manifest["unknown_request_product"] is None, "MARKET_UNKNOWN_OUTCOME_NO_RETRY")
    result = {}
    for product, record in zip(plan[: len(records)], records, strict=True):
        body = base64.b64decode(record["body_base64"], validate=True)
        receipt = record["receipt"]
        start, end = (
            datetime.fromisoformat(receipt[key])
            for key in ("request_started_at", "response_received_at")
        )
        c.b.secret_free(body, (archive.token,))
        s.need(
            receipt["product"] == product
            and receipt["body_sha256"] == s.digest(body)
            and receipt["body_bytes"] == len(body)
            and start.utcoffset() is not None
            and end.utcoffset() is not None
            and start <= end,
            "MARKET_RAW_RECEIPT",
        )
        status = receipt["http_status"]
        s.need(type(status) is int and 100 <= status <= 599, "MARKET_HTTP_STATUS")
        s.need(status == 200, "MARKET_HTTP_" + str(status) + "_RETAINED_NO_RETRY")
        c.decode_market(body)
        result[product["id"]] = {"body": body, "receipt": receipt, "product": product}
    s.need(len(result) == len(plan), "MARKET_PARTIAL_CAPTURE_NO_RETRY")
    return result


def capture(archive, ticker, source, key_factory, *, verify_only=False, get=fetch):
    archive.visibility(True)
    c.m.sha(source)
    made, result = 0, {}
    for index, plan in enumerate(groups(ticker)):
        prefix = PREFIX + f"{ticker.lower()}/{index}/"
        if archive.ref(ref(ticker, index)) is None:
            s.need(not verify_only, "MARKET_RAW_MISSING")
            s.need(archive.ref(ref(ticker, index, True)) is None, "MARKET_UNKNOWN_OUTCOME_NO_RETRY")
            key = key_factory()
            s.need(
                type(key) is str and 0 < len(key) <= 4096 and not any(ch.isspace() for ch in key),
                "MARKET_CREDENTIAL_UNAVAILABLE",
            )
            hold = c.b.create(
                archive,
                ref(ticker, index, True),
                {prefix + "reservation.json": hold_bytes(ticker, index, source)},
                c.m.BOOTSTRAP,
            )
            records, unknown = [], None
            for product in plan:
                started = datetime.now(UTC).isoformat()
                try:
                    status, body = get(product, key)
                except (TimeoutError, URLError, c.m.Stopped):
                    unknown = product["id"]
                    break
                made += 1
                c.b.secret_free(body, (key, archive.token))
                records.append(
                    {
                        "receipt": {
                            "product": product,
                            "http_status": status,
                            "body_sha256": s.digest(body),
                            "body_bytes": len(body),
                            "request_started_at": started,
                            "response_received_at": datetime.now(UTC).isoformat(),
                        },
                        "body_base64": base64.b64encode(body).decode(),
                    }
                )
                if status != 200:
                    break
            packed = gzip.compress(s.canonical({"rows": records}), mtime=0)
            parts = [packed[i : i + c.m.FILE_LIMIT] for i in range(0, len(packed), c.m.FILE_LIMIT)]
            s.need(1 <= len(parts) <= MAX_PARTS, "MARKET_RAW_PACK_BUDGET")
            payload = {prefix + f"part-{i:02}.bin": body for i, body in enumerate(parts)}
            payload[prefix + "manifest.json"] = s.canonical(
                {
                    "source_commit": source,
                    "part_count": len(parts),
                    "recorded_products": len(records),
                    "unknown_request_product": unknown,
                    "bundle_sha256": s.digest(packed),
                }
            )
            c.b.create(archive, ref(ticker, index), payload, hold)
        result.update(read_group(archive, ticker, index))
    return result, made


def guarded(source):
    c.m.sha(source)
    s.need(
        os.environ.get("GITHUB_REPOSITORY") == c.m.CODE
        and os.environ.get("GITHUB_ACTOR") == "dczhhh"
        and os.environ.get("GITHUB_EVENT_NAME") == "push"
        and os.environ.get("GITHUB_REF") == "refs/heads/main"
        and os.environ.get("GITHUB_WORKFLOW_REF") == c.m.CODE + "/" + WORKFLOW + "@refs/heads/main"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and os.environ.get("RUNNER_OS") == "Linux"
        and all(
            os.environ.get(key, "").lower() != "true"
            for key in ("ACTIONS_STEP_DEBUG", "ACTIONS_RUNNER_DEBUG")
        )
        and subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() == source,
        "MARKET_RUNNER_CONTEXT",
    )


def main():
    try:
        s.need(len(sys.argv) == 4 and sys.argv[1] in {"capture", "verify"}, "MARKET_ARGUMENTS")
        mode, ticker, source = sys.argv[1:]
        guarded(source)
        archive = c.r.CachedArchive(
            c.PacedGitHub(
                c.m.DESTINATION,
                os.environ.get("ARCHIVE_WRITE_TOKEN", ""),
                writable=mode == "capture",
            )
        )
        # Authentication/derivation check on retained source bytes, not new HTTP.
        bodies = a.read_raw(archive, ticker)
        a.audit(archive, ticker, bodies, SOURCE_READER, verify_only=True)
        _, made = capture(
            archive,
            ticker,
            source,
            lambda: os.environ.get("MASSIVE_API_KEY", ""),
            verify_only=mode == "verify",
        )
        print(
            json.dumps(
                {
                    "status": "BEAR2022_MARKET_RETAINED_NOT_ECONOMIC_ADMISSION",
                    "ticker": ticker,
                    "new_market_requests": made,
                    "products": len(products(ticker)),
                    "plan_sha256": PLAN_HASH,
                    "engine_invoked": False,
                    "PIT_verified": False,
                    "performance_admission": False,
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception as error:
        message = str(error)
        reason = message if re.fullmatch(r"BEAR2022_[A-Z_0-9]+", message) else "MARKET_UNVERIFIED"
        print(json.dumps({"status": "BLOCKED", "reason": reason, "engine_invoked": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
