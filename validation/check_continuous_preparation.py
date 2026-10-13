"""Fixed metadata snapshots only; no network, API key, mutable path or execution permit."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

from quant_constraints.research.data.archive_contracts import digest
from quant_constraints.research.data.continuous_preparation import (
    INPUT_HASHES,
    metadata_bytes,
    prepare,
)
from quant_constraints.research.data.demand import DemandError
from quant_constraints.research.data.private_capture_cli import FLAGS


def main() -> int:
    synthetic = sys.argv[1:] == ["--synthetic"]
    filename = (
        "research-continuous-inputs-synthetic-v1.json"
        if synthetic
        else "research-continuous-inputs-v1.json"
    )
    path = Path(__file__).resolve().parents[1] / "docs/examples" / filename
    try:
        if sys.argv[1:] not in [[], ["--synthetic"]]:
            raise ValueError("unsupported validation mode")
        with path.open("rb") as stream:
            content = stream.read(32769)
        if len(content) > 32768:
            raise ValueError("bounded metadata only")
        inputs = metadata_bytes(json.loads(content))
        if synthetic:
            if any(json.loads(value).get("invented") is not True for value in inputs.values()):
                raise ValueError("invented fixture required")
            with patch.dict(
                INPUT_HASHES, {key: digest(value) for key, value in inputs.items()}, clear=True
            ):
                result = prepare(inputs)
            result["validation_input_kind"] = "invented_public_fixture"
            result["real_archive_metadata_verified"] = False
        else:
            result = prepare(inputs)
    except (OSError, ValueError, TypeError, KeyError) as error:
        result = {
            "schema_version": "research_continuous_preparation_audit_v1",
            "status": "FAIL_CLOSED",
            "code": error.code if isinstance(error, DemandError) else "CONTINUOUS_INPUT_STOPPED",
            "may_acquire_aggregates": False,
            "may_replay": False,
            **dict.fromkeys(FLAGS, False),
        }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return int(result["status"] != "PREPARATION_ONLY")


if __name__ == "__main__":
    raise SystemExit(main())
