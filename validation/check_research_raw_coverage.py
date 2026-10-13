"""Additional 5C coverage gate; leave the imported upstream evidence checker untouched."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

MODULE = "src/quant_constraints/research/data/raw_validation.py"
STATEMENT_MIN = 94.0
BRANCH_MIN = 85.0


def coverage_failures(payload: dict) -> list[str]:
    if payload.get("meta", {}).get("branch_coverage") is not True:
        return ["5C coverage evidence requires branch coverage"]
    entry = payload.get("files", {}).get(MODULE)
    if not isinstance(entry, dict) or not isinstance(entry.get("summary"), dict):
        return [f"5C module is absent from coverage evidence: {MODULE}"]
    summary = entry["summary"]
    failures = []
    for kind, minimum in (("statements", STATEMENT_MIN), ("branches", BRANCH_MIN)):
        value = summary.get(f"percent_{kind}_covered")
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
            failures.append(f"5C {kind} coverage is missing or invalid")
        elif value < minimum:
            failures.append(f"5C {kind} coverage {value:.2f}% < {minimum:.2f}%")
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
