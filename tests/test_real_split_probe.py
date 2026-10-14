"""Invented current-snapshot inputs; never count as real case acceptance."""

import json
import unittest
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from tools import real_split_probe as p


def payload(adjusted=False, *, rows=None):
    return json.dumps(
        {
            "status": "OK",
            "ticker": "SQQQ",
            "adjusted": adjusted,
            "results": rows if rows is not None else invented_rows(),
        }
    ).encode()


def invented_rows():
    rows = []
    for day, close in (("2024-11-06", 28), ("2024-11-07", 142), ("2024-11-08", 139)):
        stamp = datetime.fromisoformat(day).replace(tzinfo=p.NY)
        rows.append(
            {
                "t": int(stamp.timestamp() * 1000),
                "o": close,
                "h": close + 1,
                "l": close - 1,
                "c": close,
                "v": 100,
            }
        )
    return rows


def split_payload(*, first_ratio=1):
    return json.dumps(
        {
            "status": "OK",
            "results": [
                {
                    "ticker": "SQQQ",
                    "execution_date": "2024-11-07",
                    "split_from": 5,
                    "split_to": first_ratio,
                    "historical_adjustment_factor": 25,
                    "adjustment_type": "reverse_split",
                },
                {
                    "ticker": "SQQQ",
                    "execution_date": "2025-12-01",
                    "split_from": 5,
                    "split_to": 1,
                    "historical_adjustment_factor": 5,
                    "adjustment_type": "reverse_split",
                },
            ],
        }
    ).encode()


class ProbeContractTests(unittest.TestCase):
    def test_two_independent_future_factors_use_current_anchor(self):
        raw = p.bars(payload(), False)
        factors = p.price_factors(split_payload())
        adjusted = {
            day: {
                **row,
                **{
                    field: row[field] * (25 if day < p.EVENT_DAY else 5)
                    for field in ("o", "h", "l", "c")
                },
            }
            for day, row in raw.items()
        }
        checked = p.adjustment_check(raw, adjusted, factors)
        self.assertEqual(checked["bar_count"], 3)
        self.assertEqual(checked["max_price_residual"], "0")

    def test_wrong_event_ratio_is_rejected(self):
        with self.assertRaisesRegex(p.Blocked, "ISSUER_FACTOR_MISMATCH"):
            p.price_factors(split_payload(first_ratio=2))

    def test_factor_boundary_disagrees_with_independent_issuer_terms(self):
        raw = p.bars(payload(), False)
        factors = [(p.EVENT_DAY, Decimal(20))]
        adjusted = {
            day: {
                **row,
                **{
                    field: row[field] * (20 if day < p.EVENT_DAY else 1)
                    for field in ("o", "h", "l", "c")
                },
            }
            for day, row in raw.items()
        }
        with self.assertRaisesRegex(p.Blocked, "FACTOR_BOUNDARY_MISMATCH"):
            p.adjustment_check(raw, adjusted, factors)

    def test_using_window_end_as_anchor_is_rejected(self):
        raw = p.bars(payload(), False)
        factors = p.price_factors(split_payload())
        adjusted = {
            day: {
                **row,
                **{
                    field: row[field] * (5 if day < p.EVENT_DAY else 1)
                    for field in ("o", "h", "l", "c")
                },
            }
            for day, row in raw.items()
        }
        with self.assertRaisesRegex(p.Blocked, "ADJUSTMENT_MISMATCH"):
            p.adjustment_check(raw, adjusted, factors)

    def test_real_market_move_is_not_forced_to_zero_nav_change(self):
        expected = p.expected_values(Decimal(28), Decimal(142), after=True)
        self.assertEqual(expected["quantity"], 10)
        self.assertEqual(expected["entry_price"], 140)
        self.assertEqual(expected["basis"], 1400)
        self.assertEqual(expected["cash"], 98600)
        self.assertEqual(expected["nav"], 100020)
        self.assertEqual(expected["unrealized_pnl"], 20)

    def test_non_midnight_bucket_is_rejected(self):
        rows = invented_rows()
        rows[0]["t"] += 60000
        with self.assertRaisesRegex(p.Blocked, "DAILY_BUCKET_UNVERIFIED"):
            p.bars(payload(rows=rows), False)

    def test_duplicate_timeline_is_rejected(self):
        rows = invented_rows()
        rows[1]["t"] = rows[0]["t"]
        with self.assertRaisesRegex(p.Blocked, "BAR_ORDER_MISMATCH"):
            p.bars(payload(rows=rows), False)

    def test_missing_event_boundary_is_rejected(self):
        with self.assertRaisesRegex(p.Blocked, "EVENT_BOUNDARY_MISSING"):
            p.bars(payload(rows=invented_rows()[:1]), False)

    def test_invalid_ohlc_is_rejected(self):
        rows = invented_rows()
        rows[0]["h"] = 1
        with self.assertRaisesRegex(p.Blocked, "INVALID_OHLC"):
            p.bars(payload(rows=rows), False)

    def test_wrong_ticker_or_adjustment_is_rejected(self):
        for key, value in (("ticker", "INVENTED_OTHER"), ("adjusted", True)):
            with self.subTest(key=key), self.assertRaisesRegex(p.Blocked, "PRODUCT_MISMATCH"):
                data = json.loads(payload())
                data[key] = value
                p.bars(json.dumps(data).encode(), False)

    def test_pagination_and_duplicate_json_fields_are_rejected(self):
        data = json.loads(payload())
        data["next_url"] = "https://invalid.example/INVENTED"
        with self.assertRaisesRegex(p.Blocked, "PAGINATION_UNVERIFIED"):
            p.bars(json.dumps(data).encode(), False)
        with self.assertRaisesRegex(p.Blocked, "INVALID_JSON"):
            p.decoded(b'{"status":"OK","status":"ERROR"}')

    def test_invalid_numbers_do_not_default_to_zero(self):
        for value in (True, None, "NaN", "Infinity", -1, 0):
            with self.subTest(value=value), self.assertRaises(p.Blocked):
                p.decimal(value)

    def test_output_cannot_certify_or_print_real_prices(self):
        report = {
            "component": {
                "component_status": "PASS",
                "journal": [{"raw_close": "INVENTED_PRIVATE_PRICE"}],
                "max_absolute_errors": {},
            },
            "adjustment": {"bar_count": 3},
        }
        summary = p.public_summary(report)
        self.assertNotIn("INVENTED_PRIVATE_PRICE", json.dumps(summary))
        self.assertEqual(summary["vendor_requests"], 0)
        self.assertEqual(summary["certification"], "UNVERIFIED")
        self.assertFalse(summary["full_real_scenario_accepted"])
        self.assertFalse(summary["intraday_execution_replayed"])
        self.assertTrue(all(summary[key] is False for key in p.b.FALSE_FLAGS))

    def test_verify_never_creates_missing_report(self):
        from tests.test_private_archive_migration import FakeGitHub

        destination = FakeGitHub(p.m.DESTINATION)
        report = {
            "component": {"component_status": "PASS", "journal": [], "max_absolute_errors": {}},
            "adjustment": {"bar_count": 3},
        }
        with (
            patch.object(p, "inputs", return_value={}),
            patch.object(p, "report_from_inputs", return_value=report),
            self.assertRaisesRegex(p.Blocked, "REPORT_NOT_ARCHIVED"),
        ):
            p.execute(destination, "4" * 40, verify_only=True)
        self.assertFalse(destination.refs)


if __name__ == "__main__":
    unittest.main()
