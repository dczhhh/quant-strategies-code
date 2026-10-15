"""Entirely invented transport -> canonical Engine -> fresh private replay.

Requires the separate reviewed strategy checkout. Ordinary main-only unit runs
skip this module; CI's explicit integration job requires it and cannot skip it.
"""

import importlib.util
import os
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from tests.test_private_archive_migration import FakeGitHub
from tests.test_real_etf_2025 import documents, workbook
from tests.test_rotation_research_runner import OBSERVED, SOURCE, invented_sources
from tools import rotation_research_runner as v

try:
    SPEC = importlib.util.find_spec("ml4t.backtest.strategies.sector_rotation.research")
except ModuleNotFoundError:
    SPEC = None
READY = bool(
    SPEC
    and SPEC.origin
    and Path(SPEC.origin).resolve().is_relative_to((v.ROOT / "research-source/src").resolve())
)
if os.environ.get("ROTATION_RESEARCH_INTEGRATION_REQUIRED") == "true" and not READY:
    raise RuntimeError("Reviewed nested runtime is required; refusing a skipped integration")


def combined_documents():
    history, primary, result = [], [], None
    for index, ticker in enumerate(v.p.TICKERS):
        docs = documents(ticker, changes={"B": f"{index:09}"})
        sheets = [v.p.workbook(docs[d["id"]]["body"]) for d in v.p.DOCUMENTS[:2]]
        if result is None:
            result = deepcopy(docs)
            history = sheets[0]["dividend"][:1]
            primary = sheets[1]["2025 ICI Primary"][:13]
        row = sheets[0]["dividend"][1]
        row["C"] = f"{index:09}"
        history.append(row)
        primary += sheets[1]["2025 ICI Primary"][13:]
    for index, rows in enumerate((history, primary)):
        result[v.p.DOCUMENTS[index]["id"]]["body"] = workbook(
            "dividend" if index == 0 else "2025 ICI Primary", rows
        )
        result[v.p.DOCUMENTS[index]["id"]]["receipt"]["fetched_at"] = OBSERVED
    return result


@unittest.skipUnless(READY, "separate pinned retrospective runtime not checked out")
class RotationPipelineTests(unittest.TestCase):
    def test_invented_full_pool_normalization_engine_and_fresh_ledger_match(self):
        archive = FakeGitHub(v.m.DESTINATION)
        docs = combined_documents()
        inputs = {ticker: invented_sources(ticker)[:2] for ticker in v.p.TICKERS}
        # Distinct rising invented series guarantee real orders rather than an
        # empty no-cash pass. Every corresponding minute uses the same daily mark.
        for index, (captured, old) in enumerate(inputs.values()):
            for kind, item in (*captured.items(), ("raw", old["raw"])):
                if kind not in {"warm-raw", "minutes", "raw"}:
                    continue
                value = v.b.decode(item["body"])
                for row in value["results"]:
                    level = 100 + (row["t"] - 1722484800000) / 86400000 * (index + 1) / 100
                    for key in ("o", "h", "l", "c"):
                        row[key] = level
                item["body"] = v.m.canonical(value)

        with (
            patch.object(
                v,
                "old_inputs",
                side_effect=lambda _, ticker: (inputs[ticker][1], "DIVIDEND_SPLIT_BASIS_CONFLICT"),
            ),
            patch.object(v.a, "issuer_documents", return_value=docs),
        ):
            for ticker, (captured, _) in inputs.items():

                def fetch(product, _, captured=captured):
                    return 200, captured[product["kind"]]["body"]

                archive.events.clear()
                result = v.prepare(
                    v.u.CachedArchive(archive), ticker, SOURCE, lambda: "KEY", fetch=fetch
                )
                self.assertEqual(result["new_market_requests"], 4)
                self.assertLess(len(archive.events), 800)
                archive.events.clear()
                fresh = v.prepare(
                    v.u.CachedArchive(archive),
                    ticker,
                    SOURCE,
                    lambda: self.fail("read-only key access"),
                    readonly=True,
                )
                self.assertEqual(fresh["component_sha256"], result["component_sha256"])
                self.assertLess(len(archive.events), 800)
                self.assertTrue(all(method == "GET" for method, _ in archive.events))
        archive.events.clear()
        first = v.replay(v.u.CachedArchive(archive), SOURCE, OBSERVED)
        self.assertLess(len(archive.events), 800)
        archive.events.clear()
        second = v.replay(v.u.CachedArchive(archive), SOURCE, OBSERVED, readonly=True)
        self.assertEqual(first, second)
        self.assertLess(len(archive.events), 800)
        self.assertTrue(all(method == "GET" for method, _ in archive.events))
        self.assertEqual(first["status"], "RETROSPECTIVE_ENGINE_LEDGER_PASS")
        self.assertFalse(first["PIT_verified"] or first["performance_admission"])
        body, _, _, _ = v.read_record(
            archive, "research-data/rotation-execution-v1-" + SOURCE, v.REPORT_PREFIX + SOURCE + "/"
        )
        proof = v.b.decode(body)["report"]["engine"]
        self.assertGreater(proof["independent_ledger"]["fills_checked"], 0)
        self.assertEqual(proof["full_real_scenarios_accepted"], 0)
        self.assertFalse(proof["investor_tax_eligibility_verified"])


if __name__ == "__main__":
    unittest.main()
