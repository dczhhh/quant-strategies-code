"""Entirely invented sources; executable with the Python standard library alone."""

import copy
import json
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal

from tools import bear_2022_sources as s


def sessions(first, last):
    """Invented weekday-only calendar, not evidence of exchange completeness."""
    while first <= last:
        if first.weekday() < 5:
            yield first
        first += timedelta(days=1)


def stamp(day, hour=9):
    return int(
        datetime(day.year, day.month, day.day, hour, 30 if hour else 0, tzinfo=s.NY).timestamp()
    )


def fixture(ticker="SPY"):
    days = tuple(sessions(s.WARMUP, s.LAST))
    prices = {day: Decimal(100) + Decimal(i) / 10000 for i, day in enumerate(days)}
    split_day, tail_day = date(2022, 8, 8), date(2025, 3, 3)
    dividend_day = date(2021, 6, 1)
    core = {
        "chart": {
            "error": None,
            "result": [
                {
                    "meta": {
                        "symbol": ticker,
                        "currency": "USD",
                        "exchangeTimezoneName": "America/New_York",
                    },
                    "timestamp": [stamp(day) for day in days],
                    "indicators": {
                        "quote": [
                            {
                                "close": [
                                    str(prices[day] / (4 if day < split_day else 2)) for day in days
                                ]
                            }
                        ],
                        "adjclose": [{"adjclose": ["PRIVATE_DIAGNOSTIC_NOT_USED"] * len(days)}],
                    },
                    "events": {
                        "splits": {
                            str(stamp(split_day)): {
                                "date": stamp(split_day),
                                "numerator": 2,
                                "denominator": 1,
                                "splitRatio": "2:1",
                            }
                        },
                        "dividends": {
                            str(stamp(dividend_day)): {
                                "date": stamp(dividend_day),
                                "amount": "0.25",
                            }
                        },
                    },
                }
            ],
        }
    }
    tail = {
        "chart": {
            "error": None,
            "result": [
                {
                    "meta": {
                        "symbol": ticker,
                        "currency": "USD",
                        "exchangeTimezoneName": "America/New_York",
                    },
                    "indicators": {"quote": [{"close": ["FUTURE_PRICE_NEVER_READ"]}]},
                    "events": {
                        "splits": {
                            str(stamp(tail_day)): {
                                "date": stamp(tail_day),
                                "numerator": 2,
                                "denominator": 1,
                                "splitRatio": "2:1",
                            }
                        }
                    },
                }
            ],
        }
    }
    massive = {
        "status": "OK",
        "ticker": ticker,
        "adjusted": False,
        "results": [
            {"t": stamp(day, 0) * 1000, "c": str(prices[day])}
            for day in sessions(s.OVERLAP, s.LAST)
        ],
    }
    splits = {
        "status": "OK",
        "results": [
            {"ticker": ticker, "execution_date": str(day), "split_to": 2, "split_from": 1}
            for day in (split_day, tail_day)
        ],
    }
    return {
        "yahoo_daily": core,
        "yahoo_tail_splits": tail,
        "massive_daily": massive,
        "massive_splits": splits,
    }


def encoded(value):
    return {key: s.canonical(row) for key, row in value.items()}


class SourceQualificationTests(unittest.TestCase):
    def setUp(self):
        self.value = fixture()

    def check(self):
        return s.qualify("SPY", encoded(self.value), sessions)

    def test_plan_is_fixed_and_small(self):
        self.assertEqual(sum(len(s.products(t)) for t in s.TICKERS), 48)
        self.assertEqual(len(s.PLAN_HASH), 64)
        self.assertEqual(s.POLICY["minimum_presample_closes"], 253)
        self.assertEqual(s.products("SPY")[2]["start"], "2021-10-19")
        self.assertTrue("adjusted=false" in s.products("SPY")[2]["url"])
        with self.assertRaisesRegex(ValueError, "TICKER_SCOPE"):
            s.products("GME")

    def test_split_units_and_dividends_are_explicit_derivations(self):
        report = self.check()
        summary = report["summary"]
        self.assertFalse(summary["economic_replay_ready"])
        self.assertFalse(summary["engine_invoked"])
        self.assertEqual(summary["overlap_differences_above_original_threshold"], 0)
        self.assertEqual(summary["split_events"], 2)
        self.assertGreater(summary["presample_sessions"], 253)
        self.assertEqual(report["derivation_not_source_replacement"]["closes"][0]["close"], 100)
        cash = report["derivation_not_source_replacement"]["dividends"][0]["original_share_cash"]
        self.assertEqual(cash, 1)

    def test_every_universe_asset_has_same_parser(self):
        for ticker in s.TICKERS:
            with self.subTest(ticker=ticker):
                self.assertEqual(
                    s.qualify(ticker, encoded(fixture(ticker)), sessions)["ticker"], ticker
                )

    def test_adjusted_close_and_future_tail_prices_cannot_affect_input(self):
        before = s.canonical(self.check())
        core = self.value["yahoo_daily"]["chart"]["result"][0]
        core["indicators"]["adjclose"] = [{"adjclose": ["SECRET_UNUSED_VALUE"]}]
        self.value["yahoo_tail_splits"]["chart"]["result"][0]["indicators"] = None
        after = self.check()
        first = json.loads(before)
        self.assertEqual(
            first["derivation_not_source_replacement"],
            json.loads(s.canonical(after))["derivation_not_source_replacement"],
        )

    def test_small_price_differences_stay_visible_not_fitted(self):
        raw = self.value["massive_daily"]["results"][0]
        raw["c"] = str(Decimal(raw["c"]) + Decimal(".01"))
        report = self.check()
        self.assertEqual(report["summary"]["overlap_differences_above_original_threshold"], 1)
        row = report["derivation_not_source_replacement"]["overlap_differences"][0]
        self.assertEqual(row["difference_usd"], Decimal("-.01"))
        self.assertFalse(report["summary"]["source_prices_exactly_equivalent"])

    def test_wrong_units_are_rejected_not_rescaled_to_fit(self):
        self.value["massive_daily"]["results"][0]["c"] = "50"
        with self.assertRaisesRegex(ValueError, "SOURCE_UNIT_SANITY_FAILED"):
            self.check()

    def test_split_lists_must_independently_agree(self):
        self.value["massive_splits"]["results"][0]["split_to"] = 3
        with self.assertRaisesRegex(ValueError, "SPLIT_SOURCE_CONFLICT"):
            self.check()

    def test_malformed_split_ratio_is_not_accepted(self):
        events = self.value["yahoo_daily"]["chart"]["result"][0]["events"]["splits"]
        next(iter(events.values()))["splitRatio"] = "3:1"
        with self.assertRaisesRegex(ValueError, "YAHOO_SPLIT_CONFLICT"):
            self.check()

    def test_missing_day_is_not_forward_filled(self):
        row = self.value["yahoo_daily"]["chart"]["result"][0]
        del row["timestamp"][2]
        del row["indicators"]["quote"][0]["close"][2]
        with self.assertRaisesRegex(ValueError, "YAHOO_SESSION_COVERAGE"):
            self.check()

    def test_duplicate_day_is_not_merged(self):
        row = self.value["yahoo_daily"]["chart"]["result"][0]
        row["timestamp"][2] = row["timestamp"][1]
        with self.assertRaisesRegex(ValueError, "YAHOO_CLOSE_ORDER"):
            self.check()

    def test_missing_overlap_day_is_not_silently_ignored(self):
        self.value["massive_daily"]["results"].pop()
        with self.assertRaisesRegex(ValueError, "MASSIVE_SESSION_COVERAGE"):
            self.check()

    def test_provider_error_and_identity_conflict_are_rejected(self):
        for changed in ({"currency": "CAD"}, {"symbol": "GME"}, {"exchangeTimezoneName": "UTC"}):
            with self.subTest(changed=changed):
                self.value = fixture()
                self.value["yahoo_daily"]["chart"]["result"][0]["meta"].update(changed)
                with self.assertRaisesRegex(ValueError, "YAHOO_IDENTITY"):
                    self.check()
        self.value = fixture()
        self.value["yahoo_daily"]["chart"]["error"] = {"description": "PRIVATE_PROVIDER_ERROR"}
        with self.assertRaisesRegex(ValueError, "YAHOO_RESPONSE_ERROR"):
            self.check()

    def test_null_nonfinite_and_boolean_prices_are_rejected(self):
        for bad in (None, True, "NaN", "Infinity", "0", "-1"):
            with self.subTest(bad=bad):
                self.value = fixture()
                self.value["yahoo_daily"]["chart"]["result"][0]["indicators"]["quote"][0]["close"][
                    0
                ] = bad
                with self.assertRaises(ValueError):
                    self.check()

    def test_unsupported_capital_gain_is_not_relabelled_ordinary(self):
        self.value["yahoo_daily"]["chart"]["result"][0]["events"]["capitalGains"] = {"one": {}}
        with self.assertRaisesRegex(ValueError, "YAHOO_CAPITAL_GAIN_EVENT_UNSUPPORTED"):
            self.check()

    def test_massive_pagination_and_adjusted_bars_are_rejected(self):
        self.value["massive_daily"]["next_url"] = "https://untrusted.example/private"
        with self.assertRaisesRegex(ValueError, "MASSIVE_RESPONSE"):
            self.check()
        self.value = fixture()
        self.value["massive_daily"]["adjusted"] = True
        with self.assertRaisesRegex(ValueError, "MASSIVE_IDENTITY"):
            self.check()

    def test_decoding_checks_duplicate_keys_and_size(self):
        for body in (b'{"same":1,"same":2}', b"", b"x" * 524289):
            with self.assertRaises(ValueError):
                s.decode(body)

    def test_original_bodies_are_unchanged(self):
        before = copy.deepcopy(self.value)
        self.check()
        self.assertEqual(before, self.value)

    def test_numeric_literals_are_parsed_as_decimal(self):
        self.assertEqual(
            s.decode(b'{"n":0.123456789123456789}')["n"], Decimal("0.123456789123456789")
        )

    def test_event_dictionary_indexes_are_not_effective_timestamps(self):
        original = self.check()["derivation_not_source_replacement"]
        for name in ("yahoo_daily", "yahoo_tail_splits"):
            events = self.value[name]["chart"]["result"][0]["events"]
            for kind in events:
                events[kind] = {"publisher-record-index": next(iter(events[kind].values()))}
        report = self.check()
        self.assertEqual(report["derivation_not_source_replacement"], original)
        self.assertEqual(report["reader_audit_version"], 2)
        self.assertEqual(
            report["summary"]["event_dictionary_indexes_differing_from_effective_timestamp"], 3
        )
        self.assertTrue(
            all(
                row["original_dictionary_index"] == "publisher-record-index"
                for row in report["event_dictionary_index_audit"]
            )
        )

    def test_effective_event_timestamp_type_is_still_strict(self):
        events = self.value["yahoo_daily"]["chart"]["result"][0]["events"]["splits"]
        next(iter(events.values()))["date"] = True
        with self.assertRaisesRegex(ValueError, "YAHOO_EVENT_TIMESTAMP_TYPE"):
            self.check()

    def test_explicit_event_date_still_matches_independent_split_source(self):
        events = self.value["yahoo_daily"]["chart"]["result"][0]["events"]["splits"]
        next(iter(events.values()))["date"] += 86400
        with self.assertRaisesRegex(ValueError, "SPLIT_SOURCE_CONFLICT"):
            self.check()


if __name__ == "__main__":
    unittest.main()
