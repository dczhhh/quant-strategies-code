"""Fixed sixteen-account wiring using invented prices and original Engine."""

import copy
import gzip
import json
from datetime import date, datetime
from decimal import Decimal

import pytest

from tests.strategies.test_rotation_research import inputs, settings
from tests.test_private_archive_migration import FakeGitHub
from tools import rotation_four_year_experiment as a
from tools import rotation_four_year_metrics as metrics


@pytest.fixture(scope="module")
def replay():
    return a.matrix(inputs(), "e" * 40, input_bundle="f" * 64, settings=settings())


def test_all_sixteen_accounts_use_engine_ledger_and_matching_risk_paths(replay):
    comparison, files = replay
    assert comparison["accounts"] == 16
    assert len(comparison["matching_risk_observations"]) == 4
    assert all(count > 0 for count in comparison["matching_risk_observations"].values())
    assert comparison["annual_reset_or_return_splicing"] is False
    assert set(comparison["cells"]) == {"0.1", "1"}
    for cells in comparison["cells"].values():
        assert set(cells) == set(a.CASES)
        for row in cells.values():
            assert row["initial_cash_usd"] == "100000"
            assert row["independent_ledger"]["status"] == "RETROSPECTIVE_ENGINE_LEDGER_PASS"
    manifest = json.loads(files[a.PREFIX + "manifest.json"])
    for step in a.STEPS:
        for case in a.CASES:
            item = manifest["logical_files"][f"P{step}_{case}.json"]
            raw = gzip.decompress(b"".join(files[path] for path in item["parts"]))
            details = json.loads(raw)
            assert details["orders"] and details["result"]["fills"]
            assert details["order_admission"]
            assert details["monthly_net_trades"]
            assert details["assumptions"]["assumed_ordinary_years"] == [2022]
            if case in a.m.CASES:
                assert (
                    details["signal_oracle"]["status"]
                    == "CONDITIONAL_DECISION_AND_SUBMITTED_INTENT_ORACLE_PASS"
                )


def test_risk_path_corruption_or_duplicate_observation_fails():
    data = inputs()
    reference = {row.session: "normal" for row in data.closes}
    last = data.bars[-1].end.astimezone(a.u.NY).date()
    observed = [
        {"signal_session": day, "regime": regime}
        for day, regime in reference.items()
        if day <= last
    ]
    assert a.check_spy_path(data, reference, observed) == len(observed)
    changed = copy.deepcopy(observed)
    changed[-1]["regime"] = "other"
    with pytest.raises(ValueError, match="MATCHING_SPY_RISK_PATH"):
        a.check_spy_path(data, reference, changed)
    with pytest.raises(ValueError, match="MATCHING_SPY_RISK_PATH"):
        a.check_spy_path(data, reference, observed + observed[-1:])


def test_create_only_report_and_fresh_verify_compare_every_file(replay):
    _, files = replay
    archive = FakeGitHub(a.c.m.DESTINATION)
    with pytest.raises(ValueError, match="REPORT_MISSING"):
        a.retain(archive, files, verify_only=True)
    digest = a.retain(archive, files, verify_only=False)
    refs = copy.deepcopy(archive.refs)
    archive.events.clear()
    assert a.retain(archive, files, verify_only=True) == digest
    assert archive.refs == refs and all(method == "GET" for method, _ in archive.events)
    changed = dict(files)
    path = next(iter(changed))
    changed[path] += b"changed"
    with pytest.raises(ValueError, match="FILE_HASH_SET"):
        a.retain(archive, changed, verify_only=False)


def test_declared_hash_change_cannot_rewrite_existing_report(replay):
    _, files = replay
    archive = FakeGitHub(a.c.m.DESTINATION)
    a.retain(archive, files, verify_only=False)
    changed = dict(files)
    path = next(iter(changed))
    changed[path] += b"changed"
    manifest = json.loads(changed[a.PREFIX + "manifest.json"])
    manifest["files"][path] = a.c.m.sha256(changed[path])
    changed[a.PREFIX + "manifest.json"] = a.u.canonical(manifest)
    with pytest.raises(ValueError, match="FRESH_REPORT_BYTES"):
        a.retain(archive, changed, verify_only=False)


def test_calendar_periods_include_first_day_pnl_without_annual_reset():
    daily = [
        {"date": date(2022, 12, 30), "NAV": 90.0, "exposure": 0.5},
        {"date": date(2023, 1, 3), "NAV": 99.0, "exposure": 0.6},
        {"date": date(2023, 4, 3), "NAV": 108.0, "exposure": 0.7},
    ]
    years = metrics.periods(daily, 100)
    assert years[0]["return_pct"] == pytest.approx(-10)
    assert years[1]["start_NAV_usd"] == 90
    assert years[1]["return_pct"] == pytest.approx(20)
    quarters = metrics.periods(daily, 100, quarterly=True)
    assert [row["period"] for row in quarters] == ["2022Q4", "2023Q1", "2023Q2"]
    assert quarters[1]["return_pct"] == pytest.approx(10)


def test_fixed_factor_interaction_and_signed_monthly_trades():
    cells = {
        name: {"return_pct": value}
        for name, value in zip(("M", "M_T15R", "T15_S", "T15_SR"), (1, 3, 4, 8), strict=True)
    }
    result = metrics.factor_effects(cells)
    assert result["interaction_pp"] == 2
    assert result["rotation_15_session_main_effect_pp"] == 4
    fill = {
        "timestamp": datetime(2025, 12, 5, 10, tzinfo=a.u.NY),
        "asset": "XLK",
        "quantity": Decimal("1.2"),
        "price": Decimal(100),
        "commission": Decimal("0.35"),
        "side": "buy",
    }
    rows = metrics.monthly_net_trades([fill, {**fill, "quantity": Decimal("0.7"), "side": "sell"}])
    assert rows[0]["net_shares"] == Decimal("0.5")
    assert rows[0]["commission_usd"] == Decimal("0.70")


def test_unknown_case_and_failed_cli_do_not_publish_private_details(monkeypatch, capsys):
    with pytest.raises(ValueError, match="CASE"):
        a.run_cell(inputs(), a.q.e.assumptions("0.1"), "unknown", settings())
    monkeypatch.setattr(a.sys, "argv", ["runner", "run", "e" * 40])
    monkeypatch.setattr(
        a, "guarded", lambda _source: (_ for _ in ()).throw(ValueError("PRIVATE_BODY"))
    )
    assert a.main() == 1
    assert "PRIVATE_BODY" not in capsys.readouterr().out
