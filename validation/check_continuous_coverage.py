"""Tenth additive coverage gate; the nine previously accepted gates remain unchanged."""

import argparse
import json
import math
from pathlib import Path

MODULES = tuple(
    "src/quant_constraints/research/data/" + name + ".py"
    for name in ("continuous_preparation", "continuous_materials")
)


def coverage_failures(payload: dict) -> list[str]:
    meta = payload.get("meta")
    if not isinstance(meta, dict) or meta.get("branch_coverage") is not True:
        return ["Continuous evidence requires branch coverage"]
    files = payload.get("files")
    if not isinstance(files, dict):
        return ["Continuous evidence requires a module map"]
    failures = []
    for module in MODULES:
        entry = files.get(module)
        if not isinstance(entry, dict) or not isinstance(entry.get("summary"), dict):
            failures.append(f"Continuous module is absent: {module}")
            continue
        for metric, minimum in (("statements", 94.0), ("branches", 85.0)):
            value = entry["summary"].get(f"percent_{metric}_covered")
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
                failures.append(f"{module}: {metric} coverage is missing or invalid")
            elif value < minimum:
                failures.append(f"{module}: {metric} coverage {value:.2f}% < {minimum:.2f}%")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("coverage_json", type=Path)
    args = parser.parse_args()
    failures = coverage_failures(json.loads(args.coverage_json.read_text(encoding="utf-8")))
    for failure in failures:
        print(failure)
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
