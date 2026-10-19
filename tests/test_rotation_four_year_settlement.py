"""Independent dates around historical settlement boundaries and exceptions."""

import copy
import json
from datetime import date

import pytest

from tools import rotation_four_year_settlement as s


def test_all_1003_trade_dates_match_the_independent_reference():
    report = s.audit()
    assert report["trade_dates_verified"] == 1003
    assert report["PIT_verified"] is report["original_DTC_documents_newly_archived"] is False
    assert len(report["all_trade_to_settlement_pairs"]) == 1003
    value, days, _ = s.reference()
    assert date(2023, 11, 10) in days
    assert date(2025, 1, 9) in days
    assert s.c.CAL.session(date(2025, 1, 9)) is None
    assert s.due(date(2024, 5, 24), days) == s.due(date(2024, 5, 28), days) == date(2024, 5, 29)
    assert s.due(date(2025, 12, 31), days) == date(2026, 1, 2)
    assert days[-1] > date(2026, 1, 30)
    assert value["source_access_limitations"]


def test_using_federal_employee_observation_or_exchange_closures_is_rejected():
    value = s.reference()[0]
    for wrong in ("2023-11-10", "2025-01-09"):
        altered = copy.deepcopy(value)
        altered["closed_weekdays"] = sorted([*altered["closed_weekdays"], wrong])
        with pytest.raises(ValueError, match="OPEN_EXCEPTION"):
            s.audit(json.dumps(altered).encode())


def test_bank_holiday_or_historical_cycle_errors_cannot_pass():
    value = s.reference()[0]
    for missing in ("2023-10-09", "2024-11-11", "2025-11-11"):
        altered = copy.deepcopy(value)
        altered["closed_weekdays"].remove(missing)
        with pytest.raises(ValueError, match="REFERENCE_CONFLICT"):
            s.audit(json.dumps(altered).encode())


def test_duplicate_holidays_are_rejected():
    value = s.reference()[0]
    value["closed_weekdays"].append(value["closed_weekdays"][-1])
    with pytest.raises(ValueError, match="DUPLICATE"):
        s.reference(json.dumps(value).encode())
