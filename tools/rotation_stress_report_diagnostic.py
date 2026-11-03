"""Private-only exact report comparison; public output contains hashes/paths, never values."""

from __future__ import annotations

import json
import platform
import re
import sys
import zlib
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rotation_stress_capture as g  # noqa: E402

c, u = g.c, g.u
BASELINE_SOURCE = "962bd550e970f404b8eed7d24846a438bfd74b2e"
BASELINE_BUNDLE = "efa36368b5119d4962870885d270dd371a68a14b727b66d6122725b3266514e1"
PREFIX = "research/verification/rotation_stress_202412_202506_v1/"
REPORT_REF = "research-data/rotation-stress-202412-202506-v1-engine-report"
LOGICAL_FILES = frozenset(
    {
        "inputs.json",
        "result.json",
        "decisions.json",
        "analytics.json",
        "price-audit.json",
        "provenance.json",
    }
)
SAFE_FIELDS = frozenset(
    [
        "summary",
        "rotation",
        "funded_SPY",
        "funded_spy",
        "tax_sensitivity",
        "assumptions",
        "engine",
        "independent_ledger",
        "maximum_monetary_error",
        "trace",
        "fills",
        "corporate_actions",
        "timestamp",
        "positions",
        "cash",
        "settled_cash",
        "receivables",
        "NAV",
        "price",
        "close",
        "open",
        "high",
        "low",
        "volume",
        "quantity",
        "side",
        "commission",
        "slippage",
        "asset",
        "order_id",
        "source_commit",
        "schema",
        "sources",
        "sha256",
        "observed_at",
        "url",
        "fingerprint",
        "input_fingerprint",
        "closes",
        "bars",
        "distributions",
        "splits",
        "securities",
        "settlement_sessions",
        "decision_oracle",
        "close_sensitivity",
        "monthly",
        "decisions",
        "signals",
        "execution_targets",
        "orders",
        "risk",
        "cash_blocks",
        "maximum_target_weight_difference",
        "maximum_monthly_target_weight_difference",
        "alternate_targets",
        "targets",
        "weights",
        "scores",
        "past_verified_state",
        "equity",
        "holdings",
        "signal_session",
        "execute_session",
        "valid_until",
        "expected_orders",
        "requested",
        "available_at",
        "processed_at",
        "tax",
        "eligible_quantity",
        "net",
        "gross",
        "withholding",
        "cash_delta",
        "income_delta",
        "status",
        "scope",
        "regime",
        "below",
        "above",
        "classification",
        "classification_source_id",
        "source_id",
        "event_id",
        "ex_date",
        "record_date",
        "pay_date",
        "input_schema",
        "capture_plan_sha256",
        "inventory_bundle_sha256",
        "modules_sha256",
        "logical_files",
        "files",
        "uncompressed_sha256",
        "compressed_sha256",
        "parts",
        "original_regime",
        "alternate_regime",
        "ranking_changed",
        "selected_assets_changed",
        "end_NAV_usd",
        "return_pct",
        "maximum_daily_drawdown_pct",
        "asset_net_pnl_usd",
        "daily",
        "months",
        "exposure_matched_attribution",
        "regression",
        "up_capture",
        "down_capture",
        "fixed_holdings_mark_sensitivity",
        "per_asset",
        "rows",
        "pairs",
        "statistics",
        "reference_prices",
    ]
) | frozenset(c.p.TICKERS)


def safe_key(key):
    return key if key in SAFE_FIELDS else "field_sha256:" + c.m.sha256(key.encode())[:12]


def decode(body):
    def pairs(items):
        c.p.need(len(items) == len({k for k, _ in items}), "DIAGNOSTIC_DUPLICATE_JSON_KEY")
        return dict(items)

    def nonfinite(_):
        c.p.need(False, "DIAGNOSTIC_NONFINITE_JSON")

    return json.loads(body, parse_float=Decimal, object_pairs_hook=pairs, parse_constant=nonfinite)


def load_original(archive):
    """Only the exact failed-v1 report, its bounded paths and bootstrap parent may be read."""
    archive.visibility(True)
    revision = archive.ref(REPORT_REF)
    c.p.need(revision is not None, "BASELINE_STRESS_REPORT_MISSING")
    commit = archive.request("GET", "/git/commits/" + c.m.sha(revision))
    c.p.need(commit is not None, "BASELINE_STRESS_REPORT_COMMIT_MISSING")
    tree = archive.request("GET", "/git/trees/" + c.m.sha(commit["tree"]["sha"]) + "?recursive=1")
    c.p.need(tree is not None and tree.get("truncated") is False, "BASELINE_REPORT_TREE_INVALID")
    paths = {row["path"] for row in tree["tree"] if row["type"] != "tree"}
    c.p.need(0 < len(paths) <= 49, "BASELINE_REPORT_PATH_BUDGET_INVALID")
    for path in paths:
        c.p.need(path.startswith(PREFIX), "BASELINE_REPORT_PATH_INVALID")
        name = path.removeprefix(PREFIX)
        if name == "manifest.json":
            continue
        match = re.fullmatch(r"(.+\.json)\.gz\.part-0([0-7])\.bin", name)
        c.p.need(match is not None and match[1] in LOGICAL_FILES, "BASELINE_REPORT_PATH_INVALID")
    saved, parent = c.b.read(archive, REPORT_REF, paths)
    c.p.need(parent == c.m.BOOTSTRAP, "BASELINE_REPORT_PARENT_INVALID")
    digest = c.m.sha256(u.canonical({p: c.m.sha256(v) for p, v in sorted(saved.items())}))
    c.p.need(digest == BASELINE_BUNDLE, "BASELINE_REPORT_BUNDLE_CONFLICT")
    manifest = c.b.decode(saved[PREFIX + "manifest.json"])
    c.p.need(manifest["source_commit"] == BASELINE_SOURCE, "BASELINE_REPORT_SOURCE_CONFLICT")
    unpack(saved)
    return saved


def unpack(payload):
    manifest = c.b.decode(payload[PREFIX + "manifest.json"])
    c.p.need(set(manifest["logical_files"]) == LOGICAL_FILES, "DIAGNOSTIC_LOGICAL_FILES_INVALID")
    c.p.need(
        manifest["files"]
        == {p: c.m.sha256(v) for p, v in payload.items() if p != PREFIX + "manifest.json"},
        "DIAGNOSTIC_REPORT_FILE_HASH_CONFLICT",
    )
    result = {}
    for name, info in manifest["logical_files"].items():
        c.p.need(0 < len(info["parts"]) <= 8, "DIAGNOSTIC_PART_BUDGET_INVALID")
        c.p.need(
            info["parts"]
            == [PREFIX + name + f".gz.part-{i:02}.bin" for i in range(len(info["parts"]))],
            "DIAGNOSTIC_PART_ORDER_INVALID",
        )
        packed = b"".join(payload[p] for p in info["parts"])
        c.p.need(c.m.sha256(packed) == info["compressed_sha256"], "DIAGNOSTIC_PACKED_HASH_CONFLICT")
        raw = g.bounded_gunzip(packed, 64 * 1024 * 1024)
        c.p.need(
            c.m.sha256(raw) == info["uncompressed_sha256"], "DIAGNOSTIC_EXPANDED_HASH_CONFLICT"
        )
        result[name] = (raw, packed)
    return result


def differences(left, right, limit=30):
    paths, kinds, bins = [], Counter(), Counter()

    def record(path, kind, delta=None):
        kinds[kind] += 1
        if len(paths) < limit:
            paths.append({"path": list(path), "kind": kind})
        if delta is not None:
            bins[
                "abs_le_1e-12"
                if delta <= Decimal("1E-12")
                else "abs_le_1e-8"
                if delta <= Decimal("1E-8")
                else "abs_above_1e-8"
            ] += 1

    def walk(a, b, path):
        numeric = (
            isinstance(a, (int, Decimal))
            and not isinstance(a, bool)
            and isinstance(b, (int, Decimal))
            and not isinstance(b, bool)
        )
        if numeric:
            if a != b:
                record(path, "numeric_value_changed", abs(Decimal(a) - Decimal(b)))
            elif type(a) is not type(b):
                record(path, "numeric_type_changed")
        elif type(a) is not type(b):
            record(path, "type_changed")
        elif isinstance(a, dict):
            for key in sorted(a.keys() | b.keys()):
                if key not in a or key not in b:
                    record((*path, safe_key(key)), "field_presence_changed")
                else:
                    walk(a[key], b[key], (*path, safe_key(key)))
        elif isinstance(a, list):
            if len(a) != len(b):
                record(path, "list_length_changed")
            for index, (first, second) in enumerate(zip(a, b)):
                walk(first, second, (*path, index))
        elif a != b:
            kind = "string_changed" if isinstance(a, str) else "value_changed"
            if isinstance(a, str) and isinstance(b, str) and len(a) <= 128 and len(b) <= 128:
                try:
                    if (
                        Decimal(a).is_finite()
                        and Decimal(b).is_finite()
                        and Decimal(a) == Decimal(b)
                    ):
                        kind = "exact_decimal_value_same_text_different"
                except InvalidOperation:
                    pass
            record(path, kind)

    walk(left, right, ())
    return {
        "difference_kind_counts": dict(sorted(kinds.items())),
        "numeric_difference_bins_not_tolerances": dict(sorted(bins.items())),
        "first_difference_paths_no_values": paths,
    }


def declared_changes_only(name, before, after, source):
    """Exact values only: two report metadata fields and the reproduced zero-text bug."""
    allowed = Counter()

    def walk(a, b, path):
        if (
            name == "result.json"
            and path == ("schema",)
            and a == "rotation_stress_result_v1"
            and b == "rotation_stress_result_v2"
        ):
            allowed["declared_report_schema_revision"] += 1
            return True
        if (
            name == "result.json"
            and path == ("source_commit",)
            and a == BASELINE_SOURCE
            and b == source
        ):
            allowed["declared_operator_revision"] += int(a != b)
            return True
        if type(a) is not type(b):
            return False
        if isinstance(a, dict):
            return a.keys() == b.keys() and all(walk(a[k], b[k], (*path, k)) for k in sorted(a))
        if isinstance(a, list):
            return len(a) == len(b) and all(
                walk(x, y, (*path, i)) for i, (x, y) in enumerate(zip(a, b, strict=True))
            )
        if a == b:
            return not isinstance(a, Decimal) or a.as_tuple() == b.as_tuple()
        sensitivity_path: tuple[object, ...] = (
            path
            if name == "decisions.json"
            else (
                path[3:]
                if name == "result.json"
                and len(path) > 4
                and path[0] == "tax_sensitivity"
                and path[1] in {"0", "0.30"}
                and path[2] == "decision_oracle"
                else ()
            )
        )
        zero_path = (
            (
                len(sensitivity_path) == 4
                and sensitivity_path[:2] == ("close_sensitivity", "monthly")
                and all(type(index) is int for index in sensitivity_path[2:3])
                and sensitivity_path[3:] == ("maximum_target_weight_difference",)
            )
            or sensitivity_path
            == ("close_sensitivity", "summary", "maximum_monthly_target_weight_difference")
            or (
                name == "result.json"
                and path
                == (
                    "summary",
                    "conditional_signal_close_sensitivity",
                    "maximum_monthly_target_weight_difference",
                )
            )
        )
        if (
            zero_path
            and path[-1]
            in {"maximum_target_weight_difference", "maximum_monthly_target_weight_difference"}
            and isinstance(a, str)
        ):
            try:
                if Decimal(a) == Decimal(b) == 0:
                    allowed["exact_zero_text_representation_only"] += 1
                    return True
            except InvalidOperation:
                pass
        return False

    same = walk(before, after, ())
    return same, dict(sorted(allowed.items()))


def compare_to_baseline(archive, logical, source):
    original = load_original(archive)
    old = unpack(original)
    c.p.need(set(logical) == LOGICAL_FILES, "DIAGNOSTIC_LOGICAL_FILES_INVALID")
    files = {}
    semantics_match = True
    for name in sorted(LOGICAL_FILES):
        before, after = old[name][0], logical[name]
        same, allowed = declared_changes_only(name, decode(before), decode(after), source)
        if name not in {"result.json", "decisions.json"}:
            same = before == after
        semantics_match &= same
        files[name] = {
            "uncompressed_bytes_equal": before == after,
            "exact_values_equal_except_declared_changes": same,
            "declared_change_counts": allowed,
            "original_uncompressed_sha256": c.m.sha256(before),
            "rebuilt_uncompressed_sha256": c.m.sha256(after),
            **differences(decode(before), decode(after)),
        }
    old_manifest = c.b.decode(original[PREFIX + "manifest.json"])
    return {
        "status": "STRESS_REPORT_DIAGNOSTIC_COMPLETE_NOT_ACCEPTED",
        "semantics_match": semantics_match,
        "input_bytes_exactly_equal": files["inputs.json"]["uncompressed_bytes_equal"],
        "baseline_source_commit": BASELINE_SOURCE,
        "baseline_bundle_sha256": BASELINE_BUNDLE,
        "zero_text_ambiguity_witness": zero_text_witness(decode(logical["decisions.json"])),
        "logical_files": files,
        "baseline_modules_sha256": old_manifest["modules_sha256"],
        "runtime": {
            "python": platform.python_version(),
            "platform": sys.platform,
            "zlib_build": zlib.ZLIB_VERSION,
            "zlib_runtime": zlib.ZLIB_RUNTIME_VERSION,
        },
        "expected_operator_hash_change_for_diagnostic_code": True,
        "strict_byte_comparison_is_not_waived": True,
        "new_vendor_requests": 0,
        "new_official_requests": 0,
        "PIT_verified": False,
        "performance_admission": False,
        "full_real_scenarios_accepted": 0,
    }


def zero_text_witness(decisions):
    """Only zero spellings and research dates: evidence of the original max-tie defect."""
    originals = {row["signal_session"]: row["targets"] for row in decisions.get("decisions", [])}
    result = []
    for row in decisions.get("close_sensitivity", {}).get("monthly", []):
        day = row.get("signal_session", "")
        if day not in originals or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            continue
        old, new = originals[day], row.get("alternate_targets", {})
        values = [
            abs(Decimal(str(old.get(a, 0))) - Decimal(str(new.get(a, 0))))
            for a in sorted(old.keys() | new.keys())
        ]
        if values and max(values) == 0:
            spellings = sorted({str(value) for value in values})
            if len(spellings) > 1:
                c.p.need(
                    all(re.fullmatch(r"0(?:\.0+|E-\d+)?", v) for v in spellings),
                    "DIAGNOSTIC_ZERO_TEXT_INVALID",
                )
                result.append({"signal_session": day, "equal_zero_spellings": spellings})
    return {"monthly_equal_zero_ties_with_multiple_spellings": len(result), "examples": result[:3]}


class ReportDivergence(RuntimeError):
    def __init__(self, diagnostic):
        super().__init__("BASELINE_STRESS_SEMANTICS_CHANGED")
        self.diagnostic = diagnostic
