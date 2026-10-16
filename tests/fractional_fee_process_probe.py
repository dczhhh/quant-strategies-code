"""Independent processes compare every invented report byte; no real account data."""

import base64
import io
import math
import os
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from decimal import Decimal

from pytest import MonkeyPatch

from tests.test_private_archive_migration import FakeGitHub
from tests.test_rotation_stress_inputs import stress_sources
from tools import rotation_stress_tenths_compare as f


def build():
    with MonkeyPatch.context() as patch:
        docs, old_market, new_market, _ = stress_sources.__wrapped__(patch)
        for doc in docs.values():
            output = io.BytesIO()
            with (
                zipfile.ZipFile(io.BytesIO(doc["body"])) as old,
                zipfile.ZipFile(output, "w") as fixed,
            ):
                for name in sorted(old.namelist()):
                    fixed.writestr(zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0)), old.read(name))
            doc["body"] = output.getvalue()
        for ordinal, ticker in enumerate(f.c.p.TICKERS):
            for held in old_market[ticker] + list(new_market[ticker].values()):
                product = held["product"] if "product" in held else held["receipt"]["product"]
                if product["kind"] not in {"daily", "minute"}:
                    continue
                value = f.c.decode_market(held["body"])
                for row in value["results"]:
                    day = datetime.fromtimestamp(row["t"] / 1000, UTC).astimezone(f.u.NY).date()
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
                held["body"] = f.u.canonical(value)
        archive = FakeGitHub(f.c.m.DESTINATION)

        def builder(factory, source, audit):
            return f.x.build_inputs(
                factory,
                source,
                audit,
                docs=(docs, {"status": "UNKNOWN_REQUEST_OUTCOME_RETAINED"}),
                published_at=datetime(2026, 10, 16, tzinfo=UTC),
            )

        def invented_control(_archive, _inputs, _normalized, control):
            patch.setattr(
                f,
                "BASELINE_ROTATION_NAV",
                Decimal(str(control["whole"]["rotation"]["trace"][-1]["NAV"])),
            )
            patch.setattr(
                f,
                "BASELINE_FUNDED_SPY_NAV",
                Decimal(str(control["whole"]["funded_SPY"]["trace"][-1]["NAV"])),
            )
            return {
                "legacy_controls_exactly_reproduced": True,
                "status": "INVENTED_CONTROL_ONLY_NOT_REAL_ARCHIVE_VERIFICATION",
            }

        _, payload = f.build(
            lambda: archive, "b" * 40, input_builder=builder, prior_checker=invented_control
        )
        first = f.retain_report(archive, payload)
        assert f.retain_report(archive, payload, verify_only=True) == first
        return {k: base64.b64encode(v).decode() for k, v in sorted(payload.items())}


def main(mode):
    if mode == "single":
        return build()
    assert mode == "compare"

    def run(seed):
        return subprocess.check_output(
            [sys.executable, "-m", "tests.fractional_fee_process_probe", "single"],
            env=os.environ | {"PYTHONHASHSEED": str(seed)},
        )

    with ThreadPoolExecutor(2) as pool:
        first, second = list(pool.map(run, (1, 33)))
    assert first == second, "Invented compressed fee-report bytes differ across hash seeds"
    return {"status": "INVENTED_FRACTIONAL_FEE_REPORT_BYTES_IDENTICAL", "hash_seeds": [1, 33]}


if __name__ == "__main__":
    print(f.u.canonical(main(sys.argv[1])).decode())
