"""Published date facts, including exchange-open/non-settlement weekdays."""

import json
import unittest
from datetime import date
from pathlib import Path

from tools import bear_2022_calendar as c

PATH = Path(__file__).resolve().parents[1] / "config/research/bear_2022_settlement_reference.json"


class SettlementReferenceTests(unittest.TestCase):
    def test_published_pairs_and_year_end_boundary(self):
        days = c.sessions(PATH.read_bytes())
        self.assertEqual(days[0], date(2022, 1, 3))
        self.assertEqual(days[-1], date(2023, 1, 6))
        for closed in (date(2022, 4, 15), date(2022, 10, 10), date(2022, 11, 11), date(2023, 1, 2)):
            self.assertNotIn(closed, days)
        self.assertIn(date(2022, 11, 25), days)

    def test_bank_holiday_cannot_silently_become_settlement_session(self):
        document = json.loads(PATH.read_bytes())
        document["closed_weekdays"].remove("2022-10-10")
        with self.assertRaisesRegex(ValueError, "SETTLEMENT_REFERENCE_PAIR_MISMATCH"):
            c.sessions(json.dumps(document).encode())

    def test_reference_cannot_be_relabelled_T1(self):
        document = json.loads(PATH.read_bytes())
        document["settlement_cycle"] = "T+1"
        with self.assertRaisesRegex(ValueError, "SETTLEMENT_REFERENCE_SCHEMA"):
            c.sessions(json.dumps(document).encode())


if __name__ == "__main__":
    unittest.main()
