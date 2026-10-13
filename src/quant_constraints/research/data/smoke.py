"""Opt-in, memory-only Basic permissions preflight; never an archive or truth certificate."""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Never
from urllib.parse import urlencode

from quant_constraints.calendar import NY, SessionCalendar

from .archive_contracts import RETRY_STATUSES, ArchiveError, digest, reject
from .contracts.errors import timestamp
from .massive import MAX_RESPONSE_BYTES, HTTPResponse, check_credential_content, http_get

LIMIT = 50000
SPLIT_DAYS = (date(2025, 11, 14), date(2025, 11, 17))
PENDING = (
    "license_unverified",
    "private_archive_and_replay_pending",
    "pagination_unverified",
    "raw_factor_unverified",
    "identity_and_corporate_action_PIT_unverified",
    "macro_calendar_PIT_unverified",
    "trusted_entry_blocked",
)


@dataclass(frozen=True, slots=True)
class SmokeCase:
    symbol: str
    day: date
    frequency: str
    adjusted: bool = False

    def bounds(self) -> tuple[datetime, datetime]:
        session = SessionCalendar().session(self.day)
        if session is None:
            reject("INVALID_REQUEST", "day", "A completed NYSE trading day is required")
        if self.frequency == "1m":
            return session.market_open, session.market_close
        first = datetime.combine(self.day, datetime.min.time(), NY)
        return first, datetime.combine(self.day + timedelta(days=1), datetime.min.time(), NY)

    def validate(self, now: datetime) -> None:
        today = timestamp(now, "now").astimezone(NY).date()
        try:
            cutoff = today.replace(year=today.year - 2)
        except ValueError:
            cutoff = today.replace(year=today.year - 2, day=28)
        if (
            self.symbol not in {"AAPL", "NFLX"}
            or self.frequency not in {"1m", "1d"}
            or type(self.adjusted) is not bool
            or type(self.day) is not date
            or not cutoff < self.day < today
        ):
            reject("INVALID_REQUEST", "case", "Case exceeds the completed Basic smoke scope")
        self.bounds()

    def url(self) -> str:
        first, end = self.bounds()
        span = "minute" if self.frequency == "1m" else "day"
        query = urlencode({"adjusted": str(self.adjusted).lower(), "sort": "asc", "limit": LIMIT})
        return (
            f"https://api.massive.com/v2/aggs/ticker/{self.symbol}/range/1/{span}/"
            f"{int(first.timestamp() * 1000)}/{int(end.timestamp() * 1000) - 1}?{query}"
        )


def cases_for(suite: str, day: date | None, now: datetime) -> tuple[SmokeCase, ...]:
    if suite == "basic":
        if day is None:
            day = timestamp(now, "now").astimezone(NY).date() - timedelta(days=1)
            for _ in range(10):
                if SessionCalendar().session(day) is not None:
                    break
                day -= timedelta(days=1)
        cases = tuple(
            SmokeCase(symbol, day, frequency)
            for symbol in ("AAPL", "NFLX")
            for frequency in ("1m", "1d")
        )
    elif suite == "split" and day is None:
        cases = tuple(
            SmokeCase("NFLX", split_day, frequency, adjusted)
            for split_day in SPLIT_DAYS
            for frequency in ("1m", "1d")
            for adjusted in (False, True)
        )
    else:
        reject("INVALID_REQUEST", "suite", "Choose basic or split; split has fixed event dates")
    for case in cases:
        case.validate(now)
    return cases


def _inspect(body: bytes, case: SmokeCase) -> dict:
    try:
        page = json.loads(
            body, parse_constant=lambda _: reject("PAGE_INVALID", "json", "Nonfinite JSON")
        )
    except (ValueError, UnicodeError):
        reject("PAGE_INVALID", "json", "Invalid provider JSON")
    if not isinstance(page, dict) or page.get("status") not in {"OK", "DELAYED"}:
        reject("PAGE_INVALID", "status", "No successful provider acknowledgement")
    if page.get("ticker") != case.symbol or page.get("adjusted") is not case.adjusted:
        reject("PAGE_INVALID", "identity", "Ticker/adjustment differs from the requested case")
    if not isinstance(page.get("request_id"), str) or not page["request_id"]:
        reject("PAGE_INVALID", "request_id", "Provider request identity is missing")
    if "next_url" in page:
        reject("PAGINATION_UNVERIFIED", "next_url", "Preflight never follows a continuation")
    rows = page.get("results", [])
    count, query = page.get("resultsCount"), page.get("queryCount")
    if (
        not isinstance(rows, list)
        or type(count) is not int
        or type(query) is not int
        or not len(rows) == count <= query < LIMIT
    ):
        reject("PAGE_INVALID", "counts", "Invalid counts or ambiguous exhaustion at the limit")
    first, end = case.bounds()
    lower, upper = int(first.timestamp() * 1000), int(end.timestamp() * 1000)
    stamps: list[int] = []
    for row in rows:
        if not isinstance(row, dict) or type(row.get("t")) is not int:
            reject("DATA_INVALID", "row", "Expected aggregate record and integer timestamp")
        stamp = row["t"]
        if (
            not lower <= stamp < upper
            or stamp % 60000
            or (case.frequency == "1d" and stamp != lower)
            or (stamps and stamp <= stamps[-1])
        ):
            reject("DATA_INVALID", "t", "Out-of-session, duplicate, unordered or misaligned bar")
        for field in ("o", "h", "l", "c", "v"):
            value = row.get(field)
            try:
                finite = isinstance(value, int | float) and math.isfinite(value)
            except OverflowError:
                finite = False
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not finite
                or value < 0
                or (field != "v" and value == 0)
            ):
                reject("DATA_INVALID", "ohlcv", "Invalid aggregate values")
        if not row["l"] <= min(row["o"], row["c"]) <= max(row["o"], row["c"]) <= row["h"]:
            reject("DATA_INVALID", "ohlc", "Inconsistent price bounds")
        stamps.append(stamp)
    if case.frequency == "1d" and len(rows) > 1:
        reject("DATA_INVALID", "count", "More than one daily bar")
    if not rows and query != 0:
        reject("PAGE_INVALID", "results", "Empty page has an unexplained base count")
    if not rows:
        reject("EMPTY_UNKNOWN", "results", "Empty response is not proof of zero market volume")
    return {
        "response_adjusted": page["adjusted"],
        "provider_request_id_sha256": digest(page["request_id"].encode()),
        "query_count": query,
        "bar_count": count,
        "first_bar_at": datetime.fromtimestamp(stamps[0] / 1000, UTC).isoformat(),
        "last_bar_at": datetime.fromtimestamp(stamps[-1] / 1000, UTC).isoformat(),
        "missing_rth_minutes": int((end - first).total_seconds() / 60) - count
        if case.frequency == "1m"
        else None,
        "market_completeness": "unverified",
        "bar_digest": digest(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()),
    }


class SmokeClient:
    """No provider body, URL, header or exception string enters a public report."""

    def __init__(
        self,
        *,
        transport: Callable[[str, dict[str, str], float], HTTPResponse] = http_get,
        api_key: str | None = None,
        retries: int = 2,
        minimum_interval: float = 13,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleeper: Callable[[float], None] = time.sleep,
    ):
        if (
            type(retries) is not int
            or not 0 <= retries <= 2
            or isinstance(minimum_interval, bool)
            or not isinstance(minimum_interval, int | float)
            or not 12 <= minimum_interval <= 60
        ):
            reject("INVALID_REQUEST", "limits", "Bounded retries and at least 12s/request required")
        self._key = os.environ.get("MASSIVE_API_KEY") if api_key is None else api_key
        if self._key is not None and (
            not isinstance(self._key, str) or not self._key or any(c.isspace() for c in self._key)
        ):
            reject("INVALID_REQUEST", "credential", "Invalid environment credential")
        self._transport, self._clock, self._sleep = transport, clock, sleeper
        self._retries, self._interval = retries, minimum_interval

    def run(self, *, suite: str = "basic", day: date | None = None) -> dict:
        started = timestamp(self._clock(), "started_at")
        cases = cases_for(suite, day, started)
        reports: list[dict] = []
        attempts: list[dict] = []
        for case in cases if self._key else ():
            report = asdict(case) | {"day": case.day.isoformat(), "code": "TRANSPORT_FAILED"}
            for ordinal in range(self._retries + 1):
                if len(attempts) >= 16:
                    report["code"] = "REQUEST_BUDGET_EXHAUSTED"
                    break
                self._sleep(self._interval)
                at = timestamp(self._clock(), "request_started_at")
                try:
                    response = self._transport(
                        case.url(),
                        {"Accept": "application/json", "Authorization": f"Bearer {self._key}"},
                        20,
                    )
                except Exception:
                    # Transport diagnostics can contain the key, URL or provider body.
                    response = HTTPResponse(0, b"")
                if (
                    not isinstance(response, HTTPResponse)
                    or type(response.status) is not int
                    or not isinstance(response.body, bytes)
                    or len(response.body) > MAX_RESPONSE_BYTES
                ):
                    report["code"] = "PAGE_INVALID"
                    attempts.append(
                        {"case": len(reports), "status": None, "started_at": at.isoformat()}
                    )
                    break
                attempts.append(
                    {
                        "case": len(reports),
                        "status": response.status,
                        "started_at": at.isoformat(),
                        "ended_at": timestamp(self._clock(), "request_ended_at").isoformat(),
                    }
                )
                report["http_status"] = response.status
                try:
                    check_credential_content(response.body, self._key)
                    if response.status == 200:
                        report.update(_inspect(response.body, case))
                        report.update(code="PREFLIGHT_OBSERVED", l0_sha256=digest(response.body))
                        break
                except ArchiveError as error:
                    report["code"] = error.code
                    break
                if response.status in {401, 403}:
                    report["code"] = (
                        "AUTHENTICATION_DENIED" if response.status == 401 else "PERMISSION_GAP"
                    )
                    break
                if response.status not in RETRY_STATUSES:
                    report["code"] = "TRANSPORT_REJECTED"
                    break
                report["code"] = "RATE_LIMITED" if response.status == 429 else "TRANSPORT_FAILED"
                if ordinal < self._retries:
                    delay = 0.5 * 2**ordinal
                    try:
                        declared = float(
                            {k.lower(): v for k, v in response.headers}.get("retry-after", "0")
                        )
                        if 0 < declared <= 60:
                            delay = max(delay, declared)
                    except (ValueError, TypeError):
                        pass
                    self._sleep(delay)
            reports.append(report)
        return {
            "schema_version": "massive_basic_smoke_v1",
            "suite": suite,
            "secret_mapped": bool(self._key),
            "status": "secret_missing"
            if not self._key
            else "preflight_pass"
            if all(r["code"] == "PREFLIGHT_OBSERVED" for r in reports)
            else "access_unverified",
            "started_at": started.isoformat(),
            "ended_at": timestamp(self._clock(), "ended_at").isoformat(),
            "request_count": len(attempts),
            "attempts": attempts,
            "cases": reports,
            "plan": "Stocks Basic advertised; actual rights observed only for requested windows",
            "data_storage": "none_memory_only",
            "archive_hash": None,
            "replay_digest": None,
            "archive_ready": False,
            "raw_verified": False,
            "PIT_verified": False,
            "trusted_entry": False,
            "pending_evidence": PENDING,
        }


class SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        reject("INVALID_REQUEST", "arguments", "Invalid CLI arguments")


def main() -> int:
    parser = SafeParser(description=__doc__)
    parser.add_argument("--suite", choices=("basic", "split"), default="basic")
    parser.add_argument("--day", type=date.fromisoformat)
    try:
        args = parser.parse_args()
        report = SmokeClient().run(suite=args.suite, day=args.day)
    except Exception:
        # Never emit a traceback with credential-bearing transport locals.
        print(
            '{"schema_version":"massive_basic_smoke_v1","status":"preflight_failed","code":"INVALID_REQUEST_OR_INTERNAL_FAILURE","data_storage":"none_memory_only"}'
        )
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "preflight_pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
