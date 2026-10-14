"""Invented runtime fixtures, run only against the immutable feature runtime pin."""

import unittest
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch

from tests.test_real_split_probe import payload
from tools import real_split_probe as p


class PinnedComponentTests(unittest.TestCase):
    def test_native_component_uses_independent_expectations_and_keeps_pit_rejected(self):
        result = p.mechanical_probe(p.bars(payload(), False), datetime(2026, 10, 14, tzinfo=UTC))
        self.assertEqual(result["component_status"], "PASS")
        self.assertTrue(result["order_and_oco_units_pass"])
        self.assertTrue(result["cash_and_reservations_unchanged"])
        self.assertTrue(result["fractional_cash_rejected"])
        self.assertTrue(result["reorganization_kind_rejected"])
        self.assertTrue(result["production_PIT_preflight_rejected"])
        self.assertIsNone(result["historical_known_at"])
        self.assertEqual(result["journal"][1]["actual"]["nav"], "100020.0")
        self.assertEqual(result["journal"][1]["actual"]["quantity"], "10.0")
        self.assertTrue(
            all(
                Decimal(error) <= Decimal("0.00000001")
                for error in result["max_absolute_errors"].values()
            )
        )

    def test_broken_native_quantity_cannot_pass_independent_oracle(self):
        from quant_constraints.corporate_actions import CorporateActionProcessor

        original = CorporateActionProcessor.split

        def broken(processor, asset, event, observed):
            original(processor, asset, event, observed)
            processor.broker.positions[asset].quantity += 1

        with patch.object(CorporateActionProcessor, "split", broken), self.assertRaises(p.Blocked):
            p.mechanical_probe(p.bars(payload(), False), datetime(2026, 10, 14, tzinfo=UTC))


if __name__ == "__main__":
    unittest.main()
