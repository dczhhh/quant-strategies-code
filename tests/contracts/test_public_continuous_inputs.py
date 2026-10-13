"""Public fixture checks cannot certify actual private inputs or change runtime pins."""

import json
import sys
from copy import deepcopy

from quant_constraints.research.data import continuous_preparation as preparation
from validation import check_continuous_preparation as checker


def test_explicit_invented_mode_restores_production_fingerprints(monkeypatch, capsys):
    before = deepcopy(preparation.INPUT_HASHES)
    monkeypatch.setattr(sys, "argv", ["check_continuous_preparation.py", "--synthetic"])
    assert checker.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["validation_input_kind"] == "invented_public_fixture"
    assert result["real_archive_metadata_verified"] is False
    assert result["market_requests"] == 0
    assert result["may_replay"] is result["may_acquire_aggregates"] is False
    assert before == preparation.INPUT_HASHES


def test_missing_private_metadata_still_fails_closed(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["check_continuous_preparation.py"])
    assert checker.main() == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "FAIL_CLOSED"
    assert result["may_replay"] is result["may_acquire_aggregates"] is False


def test_unsupported_mode_is_not_an_arbitrary_private_path(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["check_continuous_preparation.py", "DO_NOT_PRINT_PATH"])
    assert checker.main() == 1
    output = capsys.readouterr().out
    assert json.loads(output)["status"] == "FAIL_CLOSED"
    assert "DO_NOT_PRINT_PATH" not in output
