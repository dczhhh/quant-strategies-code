"""Memory-only real-provider preflight is tested with synthetic transports only."""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import yaml

from quant_constraints.calendar import NY
from quant_constraints.research.data.archive_contracts import ArchiveError
from quant_constraints.research.data.massive import HTTPResponse
from quant_constraints.research.data.smoke import SmokeCase, SmokeClient, cases_for, main

NOW = datetime(2026, 10, 11, 4, tzinfo=UTC)
KEY = "synthetic-private-smoke-key-do-not-log"


class Transport:
    def __init__(self, mutate=None, status=200):
        self.calls = []
        self.mutate = mutate or (lambda page: page)
        self.status = status

    def __call__(self, url, headers, timeout):
        self.calls.append((url, headers, timeout))
        parts = url.split("/range/1/")
        symbol = parts[0].rsplit("/", 1)[1]
        stamp = int(parts[1].split("/")[1])
        page = {
            "status": "OK",
            "ticker": symbol,
            "adjusted": "adjusted=true" in url,
            "queryCount": 1,
            "resultsCount": 1,
            "request_id": "synthetic-request-id",
            "results": [{"t": stamp, "o": 100, "h": 101, "l": 99, "c": 100.5, "v": 123}],
        }
        return HTTPResponse(self.status, json.dumps(self.mutate(page)).encode())


def run(transport=None, **options):
    return SmokeClient(
        transport=transport or Transport(),
        api_key=KEY,
        clock=lambda: NOW,
        sleeper=lambda _: None,
        **options,
    ).run()


def test_four_basic_cases_use_bearer_no_data_persistence_and_no_certification(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    transport = Transport()
    report = run(transport)
    assert report["status"] == "preflight_pass" and report["request_count"] == 4
    assert len(report["cases"]) == 4 and report["secret_mapped"]
    assert {c["day"] for c in report["cases"]} == {"2026-10-09"}
    assert all(
        c["adjusted"] is False and c["market_completeness"] == "unverified" for c in report["cases"]
    )
    assert all(
        "adjusted=false&sort=asc&limit=50000" in u
        and KEY not in u
        and h["Authorization"] == "Bearer " + KEY
        for u, h, _ in transport.calls
    )
    assert KEY not in json.dumps(report) and '"o":' not in json.dumps(report)
    assert list(tmp_path.iterdir()) == []
    assert (
        report["data_storage"] == "none_memory_only"
        and report["archive_hash"] is None
        and report["replay_digest"] is None
    )
    assert not any(
        report[k] for k in ("archive_ready", "raw_verified", "PIT_verified", "trusted_entry")
    )
    assert "license_unverified" in report["pending_evidence"]


def test_split_suite_is_eight_fixed_raw_adjusted_cases():
    cases = cases_for("split", None, NOW)
    assert len(cases) == 8 and {c.day for c in cases} == {date(2025, 11, 14), date(2025, 11, 17)}
    assert {c.adjusted for c in cases} == {True, False}
    client = SmokeClient(
        transport=Transport(), api_key=KEY, clock=lambda: NOW, sleeper=lambda _: None
    )
    report = client.run(suite="split")
    assert report["status"] == "preflight_pass" and report["request_count"] == 8


@pytest.mark.parametrize(
    "day",
    [
        date(2026, 10, 11),
        date(2026, 10, 12),
        date(2024, 6, 10),
        date(2026, 10, 10),
        date(2026, 7, 3),
    ],
)
def test_unfinished_out_of_basic_history_and_closed_days_reject_before_transport(day):
    transport = Transport()
    client = SmokeClient(
        transport=transport, api_key=KEY, clock=lambda: NOW, sleeper=lambda _: None
    )
    with pytest.raises(ArchiveError):
        client.run(day=day)
    assert not transport.calls


@pytest.mark.parametrize(
    "day,minutes", [(date(2025, 11, 28), 210), (date(2026, 3, 6), 390), (date(2026, 3, 9), 390)]
)
def test_half_day_and_dst_use_real_session_bounds(day, minutes):
    case = SmokeCase("AAPL", day, "1m")
    first, end = case.bounds()
    assert (end - first).total_seconds() / 60 == minutes
    assert f"/{int(first.timestamp() * 1000)}/{int(end.timestamp() * 1000) - 1}?" in case.url()
    assert first.astimezone(NY).hour == 9


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "AUTHENTICATION_DENIED"),
        (403, "PERMISSION_GAP"),
        (302, "TRANSPORT_REJECTED"),
        (404, "TRANSPORT_REJECTED"),
        (429, "RATE_LIMITED"),
        (503, "TRANSPORT_FAILED"),
    ],
)
def test_status_failures_never_become_empty_market_data(status, code):
    report = run(Transport(status=status), retries=0)
    assert report["status"] == "access_unverified"
    assert all(c["code"] == code and "bar_count" not in c for c in report["cases"])


@pytest.mark.parametrize(
    "mutation,code",
    [
        ({"ticker": "OTHER"}, "PAGE_INVALID"),
        ({"adjusted": True}, "PAGE_INVALID"),
        ({"request_id": ""}, "PAGE_INVALID"),
        ({"resultsCount": 2}, "PAGE_INVALID"),
        ({"queryCount": 50000}, "PAGE_INVALID"),
        ({"queryCount": False}, "PAGE_INVALID"),
        ({"next_url": "https://api.massive.com/arbitrary?cursor=opaque"}, "PAGINATION_UNVERIFIED"),
        ({"next_url": None}, "PAGINATION_UNVERIFIED"),
        ({"results": [], "resultsCount": 0, "queryCount": 0}, "EMPTY_UNKNOWN"),
        ({"results": [], "resultsCount": 0, "queryCount": 1}, "PAGE_INVALID"),
        ({"message": KEY}, "KEY_EXPOSURE"),
        ({"authorization": "Bearer unrelated-secret"}, "KEY_EXPOSURE"),
    ],
)
def test_invalid_pages_and_credentials_are_sanitized(mutation, code):
    report = run(Transport(lambda p: p | mutation))
    assert all(c["code"] == code for c in report["cases"])
    assert KEY not in json.dumps(report) and "unrelated-secret" not in json.dumps(report)
    assert all("l0_sha256" not in c for c in report["cases"])


@pytest.mark.parametrize(
    "change", [{"t": 0}, {"o": False}, {"c": 999}, {"v": -1}, {"h": float("nan")}, {"h": 10**400}]
)
def test_bad_timestamps_and_ohlcv_reject(change):
    def mutate(page):
        page["results"][0].update(change)
        return page

    report = run(Transport(mutate))
    assert report["status"] == "access_unverified"


def test_duplicate_bars_reject_and_sparse_minutes_remain_unknown():
    def mutate(page):
        page["results"] *= 2
        page.update(resultsCount=2, queryCount=2)
        return page

    assert run(Transport(mutate))["status"] == "access_unverified"
    report = run()
    assert report["cases"][0]["missing_rth_minutes"] == 389
    assert not report["raw_verified"]


def test_rate_limit_budget_backoff_and_exception_strings_are_bounded_and_safe():
    waits = []
    client = SmokeClient(
        transport=Transport(status=429), api_key=KEY, clock=lambda: NOW, sleeper=waits.append
    )
    report = client.run(suite="split")
    assert report["request_count"] == 16 and all(w <= 60 for w in waits)
    assert sum(w == 13 for w in waits) == 16 and 0.5 in waits and 1 in waits
    assert report["cases"][-1]["code"] == "REQUEST_BUDGET_EXHAUSTED"

    def raises(*args):
        raise RuntimeError(KEY + "https://unsafe.example/private-data")

    report = run(raises, retries=0)
    assert KEY not in json.dumps(report) and "unsafe.example" not in json.dumps(report)


@pytest.mark.parametrize(
    "options",
    [
        {"minimum_interval": 11},
        {"minimum_interval": True},
        {"minimum_interval": float("nan")},
        {"retries": 3},
        {"retries": True},
    ],
)
def test_unsafe_client_limits_reject(options):
    with pytest.raises(ArchiveError):
        SmokeClient(**options)


def test_missing_environment_secret_does_not_call_native_http(monkeypatch, capsys):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    monkeypatch.setattr("sys.argv", ["smoke", "--day", "2026-10-09"])
    assert main() == 1
    assert json.loads(capsys.readouterr().out)["status"] == "secret_missing"


def test_cli_invalid_input_does_not_echo_secret_or_traceback(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["smoke", "--day", KEY])
    assert main() == 1
    output = capsys.readouterr()
    assert KEY not in output.out + output.err and "Traceback" not in output.out + output.err


@pytest.mark.parametrize(
    "response",
    [
        HTTPResponse(200, b"not json"),
        HTTPResponse(200, b"null"),
        HTTPResponse(True, b"{}"),
        "invalid transport response",
    ],
)
def test_malformed_transport_and_provider_responses_have_safe_reports(response):
    report = run(lambda *_: response, retries=0)
    assert report["status"] == "access_unverified"
    assert all(c["code"] == "PAGE_INVALID" for c in report["cases"])


def test_cli_success_only_emits_sanitized_metadata(monkeypatch, capsys):
    client = SmokeClient(
        transport=Transport(), api_key=KEY, clock=lambda: NOW, sleeper=lambda _: None
    )
    monkeypatch.setattr("quant_constraints.research.data.smoke.SmokeClient", lambda: client)
    monkeypatch.setattr("sys.argv", ["smoke"])
    assert main() == 0
    output = capsys.readouterr().out
    assert (
        json.loads(output)["status"] == "preflight_pass"
        and KEY not in output
        and '"o":' not in output
    )


def test_workflow_only_dispatches_reviewed_protected_code_and_maps_exact_secret():
    root = Path(__file__).parents[2]
    path = root / ".github/workflows/massive-smoke.yml"
    workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    guard = workflow["jobs"]["reviewed-code"]["steps"][0]
    assert (
        "refs/heads/issue-5b-archive" in guard["run"]
        and 'test "$COMMIT" = "$REVIEWED_COMMIT"' in guard["run"]
    )
    assert "required_reviewers" in guard["run"] and "secrets.massive" not in json.dumps(guard)
    job = workflow["jobs"]["permissions-preflight"]
    assert job["needs"] == "reviewed-code" and job["environment"] == "massive-smoke"
    assert job["steps"][0]["with"]["ref"] == "${{ needs.reviewed-code.outputs.commit }}"
    assert job["steps"][-1]["env"]["MASSIVE_API_KEY"] == "${{ secrets.massive }}"
    assert "${{ inputs." not in job["steps"][-1]["run"]
    assert (
        "upload-artifact" not in path.read_text()
        and "secrets.massive" not in (root / ".github/workflows/ci.yml").read_text()
    )
