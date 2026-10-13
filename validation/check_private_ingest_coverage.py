"""Additive private-repository intake coverage; all seven accepted gates remain unchanged."""

import argparse
import json
import math
from pathlib import Path

MODULE = "src/quant_constraints/research/data/private_repo_ingest.py"
STATEMENT_MIN = 94.0
BRANCH_MIN = 85.0


def coverage_failures(payload: dict) -> list[str]:
    meta = payload.get("meta")
    if not isinstance(meta, dict) or meta.get("branch_coverage") is not True:
        return ["Private repository intake evidence requires branch coverage"]
    files = payload.get("files")
    if not isinstance(files, dict):
        return ["Private repository intake evidence requires a module map"]
    entry = files.get(MODULE)
    if not isinstance(entry, dict) or not isinstance(entry.get("summary"), dict):
        return ["Private repository intake module is absent from coverage evidence"]
    failures = []
    for metric, minimum in (("statements", STATEMENT_MIN), ("branches", BRANCH_MIN)):
        value = entry["summary"].get(f"percent_{metric}_covered")
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
            failures.append(f"Private repository intake {metric} coverage is missing or invalid")
        elif value < minimum:
            failures.append(
                f"Private repository intake {metric} coverage {value:.2f}% < {minimum:.2f}%"
            )
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
