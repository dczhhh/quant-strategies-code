"""Additive 5F-0 coverage; existing global/raw/PIT/entry gates remain unchanged."""

import argparse
import json
import math
from pathlib import Path

MODULES = ("src/quant_constraints/research/data/permission.py",)
STATEMENT_MIN = 94.0
BRANCH_MIN = 85.0


def coverage_failures(payload: dict) -> list[str]:
    meta = payload.get("meta")
    if not isinstance(meta, dict) or meta.get("branch_coverage") is not True:
        return ["5F-0 coverage evidence requires branch coverage"]
    files = payload.get("files")
    if not isinstance(files, dict):
        return ["5F-0 coverage evidence requires a module map"]
    failures = []
    for module in MODULES:
        entry = files.get(module)
        if not isinstance(entry, dict) or not isinstance(entry.get("summary"), dict):
            failures.append(f"5F-0 module is absent from coverage evidence: {module}")
            continue
        for metric, minimum in (("statements", STATEMENT_MIN), ("branches", BRANCH_MIN)):
            value = entry["summary"].get(f"percent_{metric}_covered")
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
                failures.append(f"5F-0 {module} {metric} coverage is missing or invalid")
            elif value < minimum:
                failures.append(f"5F-0 {module} {metric} coverage {value:.2f}% < {minimum:.2f}%")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("coverage_json", type=Path)
    arguments = parser.parse_args()
    failures = coverage_failures(json.loads(arguments.coverage_json.read_text(encoding="utf-8")))
    for failure in failures:
        print(failure)
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
