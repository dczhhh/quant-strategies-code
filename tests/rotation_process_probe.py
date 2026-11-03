"""Cross-process probe using invented fixtures only; no archive/provider access."""

import gzip
import io
import json
import math
import os
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from decimal import Decimal

from ml4t.backtest.strategies.sector_rotation.research import retrospective_rotation_engine
from ml4t.backtest.strategies.sector_rotation.research_benchmark import retrospective_spy_benchmark
from tests.strategies.test_rotation_research import ASSUMPTIONS, inputs, settings
from tests.test_rotation_decision_oracle import changed_prices
from tools import rotation_decision_oracle as o
from tools import rotation_research_replay as u
from tools import rotation_stress_analytics as a


def stress(crash=False):
    from pytest import MonkeyPatch

    from tests.test_private_archive_migration import FakeGitHub
    from tests.test_rotation_stress_inputs import stress_sources
    from tools import rotation_stress_replay as r

    with MonkeyPatch.context() as patch:
        docs, old_market, new_market, _ = stress_sources.__wrapped__(patch)
        # Fixture workbook bytes must not contain their process's wall clock.
        for doc in docs.values():
            output = io.BytesIO()
            with (
                zipfile.ZipFile(io.BytesIO(doc["body"])) as old,
                zipfile.ZipFile(output, "w") as fixed,
            ):
                for name in sorted(old.namelist()):
                    fixed.writestr(zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0)), old.read(name))
            doc["body"] = output.getvalue()
        if crash:
            for ordinal, ticker in enumerate(r.c.p.TICKERS):
                for held in old_market[ticker] + list(new_market[ticker].values()):
                    product = held["product"] if "product" in held else held["receipt"]["product"]
                    if product["kind"] not in {"daily", "minute"}:
                        continue
                    value = r.c.decode_market(held["body"])
                    for row in value["results"]:
                        day = datetime.fromtimestamp(row["t"] / 1000, UTC).astimezone(u.NY).date()
                        t = (day - date(2023, 11, 1)).days
                        fall = min(max((day - date(2025, 2, 19)).days / 48, 0), 1)
                        rebound = min(max((day - date(2025, 4, 8)).days / 75, 0), 1)
                        market = 1 - 0.28 * fall + 0.30 * rebound
                        alpha = 0 if ticker == "SPY" else 0.07 * math.sin(t / 33 + ordinal)
                        price = 100 * (1 + t * 0.0003) * market * (1 + alpha)
                        if product["kind"] == "minute":
                            price *= 1 + 0.0002 * math.sin(t + ordinal)
                        for key in ("o", "h", "l", "c"):
                            row[key] = price
                    held["body"] = u.canonical(value)
        archive = FakeGitHub(r.c.m.DESTINATION)

        def builder(factory, source, audit):
            return r.x.build_inputs(
                factory,
                source,
                audit,
                docs=(docs, {"status": "UNKNOWN_REQUEST_OUTCOME_RETAINED"}),
                published_at=datetime(2026, 10, 16, tzinfo=UTC),
            )

        r.execute(
            lambda: archive,
            "b" * 40,
            input_builder=builder,
            baseline_checker=lambda *_: {
                "status": "INVENTED_BASELINE_ONLY",
                "semantics_match": True,
            },
        )
        commit = archive.commits[archive.refs[r.REPORT_REF]]
        tree = archive.trees[commit["tree"]["sha"]]
        saved = {row["path"]: archive.blobs[row["sha"]] for row in tree["tree"]}
        manifest = json.loads(saved[r.PREFIX + "manifest.json"])
        return {
            name: json.loads(gzip.decompress(b"".join(saved[p] for p in item["parts"])))
            for name, item in manifest["logical_files"].items()
        }


def main(mode):
    if mode == "stress_crash_compare":

        def run(seed):
            return subprocess.check_output(
                [sys.executable, "-m", "tests.rotation_process_probe", "stress_crash"],
                env=os.environ | {"PYTHONHASHSEED": str(seed)},
            )

        with ThreadPoolExecutor(2) as pool:
            first, second = list(pool.map(run, (1, 33)))
        assert first == second, (
            "Invented full crash/recovery reports differ across independent hash seeds"
        )
        return {
            "status": "INVENTED_FULL_STRESS_CROSS_PROCESS_BYTES_IDENTICAL",
            "hash_seeds": [1, 33],
        }
    if mode in {"stress", "stress_crash"}:
        return stress(mode == "stress_crash")
    source = inputs(bear=mode == "bear", end="2025-11-10")
    if mode == "replacement":

        def price(asset, day, old):
            if asset != "SPY" and day >= date(2025, 10, 30):
                return old * (
                    Decimal("1.05")
                    if asset in {"XLB", "XLC", "XLE", "XLF", "XLI"}
                    else Decimal(".99")
                )
            return old

        source = changed_prices(source, price)
    engine = retrospective_rotation_engine(source, ASSUMPTIONS, settings())
    oracle = o.DecisionOracle(engine)
    rotation = u.engine_result(engine)
    funded = u.engine_result(retrospective_spy_benchmark(source, ASSUMPTIONS))
    decision = oracle.finish()
    decision["close_sensitivity"] = o.conditional_close_sensitivity(source, oracle)
    return {
        "rotation": rotation,
        "funded": funded,
        "decision": decision,
        "analytics": a.describe(source, ASSUMPTIONS, rotation, funded, decision),
    }


if __name__ == "__main__":
    print(u.canonical(main(sys.argv[1])).decode())
