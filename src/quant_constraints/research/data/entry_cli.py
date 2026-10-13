"""Explicit mode pipeline; never imports user plugins or calls legacy Engine."""

from __future__ import annotations

import argparse
import json
import sys
from importlib import import_module
from pathlib import Path

from ml4t.backtest import BacktestConfig, Strategy
from quant_constraints import ConstraintConfig

from .entry import SyntheticRunSettings, build_research_engine, build_synthetic_test_engine
from .entry_files import _keys, load_dataset_files, read_bound_file
from .pit import PITError, _canonical
from .verification_contracts import VerificationContractError, _deny
from .verification_wire import decode_json, safe_field

CONFIG_SCHEMA = "synthetic_research_config_v1"


class _NoOrders(Strategy):
    """CLI wiring probe only; no configurable trading strategy or signal."""

    def on_data(self, timestamp, data, context, broker):
        return None


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        _deny("INVALID_CLI_ARGUMENTS", "arguments", "cli")


def main(argv: list[str] | None = None) -> int:
    try:
        parser = _Parser(description="Opt-in research admission; real evidence remains deferred")
        parser.add_argument(
            "--mode", choices=("verified_research", "synthetic_test"), required=True
        )
        parser.add_argument("--root", type=Path, required=True)
        parser.add_argument("--manifest", required=True)
        parser.add_argument("--manifest-sha256", required=True)
        parser.add_argument("--input-root-sha256", required=True)
        parser.add_argument("--config")
        parser.add_argument("--config-sha256")
        args = parser.parse_args(argv)
        files = load_dataset_files(
            args.root,
            args.manifest,
            reviewed_manifest_sha256=args.manifest_sha256,
            reviewed_root_sha256=args.input_root_sha256,
        )
        if args.mode == "verified_research":
            build_research_engine(files.snapshot)
        if args.config is None or args.config_sha256 is None:
            _deny("MISSING_CONFIG", "config", "cli")
        content, _ = read_bound_file(args.root, args.config, args.config_sha256)
        config = _keys(decode_json(content), {"schema_version", "backtest", "constraints"})
        if config["schema_version"] != CONFIG_SCHEMA:
            _deny("INVALID_CONFIG", "config", "cli")
        settings = SyntheticRunSettings(
            BacktestConfig.from_dict(config["backtest"], strict=True),
            ConstraintConfig(**config["constraints"]),
        )
        plan = build_synthetic_test_engine(
            files, settings, _NoOrders, reviewed_root_sha256=args.input_root_sha256
        )
        audit = plan.run().metrics["data_provenance_v1"]
        print(json.dumps(audit, sort_keys=True, allow_nan=False))
        return 0
    except (VerificationContractError, PITError) as error:
        code, field, phase = error.code, safe_field(error.field), getattr(error, "phase", "entry")
    except (ValueError, TypeError, OSError, AttributeError, RecursionError):
        code, field, phase = "INVALID_INPUT", "input", "cli"
    audit = {
        "schema_version": "data_provenance_v1",
        "stage": "5E-3_entry_pipeline",
        "status": "real_evidence_pending" if code == "REAL_EVIDENCE_DEFERRED" else "reject",
        "code": code,
        "field": field,
        "phase": phase,
        "may_build_engine": False,
        "raw_verified": False,
        "PIT_verified": False,
        "archive_ready": False,
        "trusted_entry": False,
    }
    import hashlib

    audit["audit_sha256"] = hashlib.sha256(_canonical(audit)).hexdigest()
    print(json.dumps(audit, sort_keys=True), file=sys.stdout)
    return 2


if __name__ == "__main__":
    # `python -m` gives this module the name __main__. Delegate to its canonical
    # import so the fixed probe has the same strategy binding as config creation.
    raise SystemExit(import_module("quant_constraints.research.data.entry_cli").main())
