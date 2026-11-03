"""Invented report bytes: authentic baseline pin, no secret/value leakage or money tolerance waiver."""

import copy
import gzip
import json
from decimal import Decimal

import pytest

from tests.test_private_archive_migration import FakeGitHub
from tools import rotation_stress_report_diagnostic as d

c, u = d.c, d.u
SOURCE = "c" * 40


def logical():
    data = dict.fromkeys(d.LOGICAL_FILES, {})
    data["inputs.json"] = {"price": "101.2345", "source_id": "INVENTED_SOURCE_VALUE"}
    data["decisions.json"] = {
        "close_sensitivity": {"monthly": [{"maximum_target_weight_difference": "0E-28"}]}
    }
    data["result.json"] = {
        "schema": "rotation_stress_result_v1",
        "source_commit": d.BASELINE_SOURCE,
        "rotation": {"cash": 10000.125, "positions": {"XLB": 10}},
    }
    return {k: u.canonical(v) for k, v in data.items()}


def payload(documents):
    result, index = {}, {}
    for name, raw in documents.items():
        packed = gzip.compress(raw, mtime=0)
        path = d.PREFIX + name + ".gz.part-00.bin"
        result[path] = packed
        index[name] = {
            "uncompressed_sha256": c.m.sha256(raw),
            "compressed_sha256": c.m.sha256(packed),
            "parts": [path],
        }
    result[d.PREFIX + "manifest.json"] = u.canonical(
        {
            "source_commit": d.BASELINE_SOURCE,
            "logical_files": index,
            "files": {p: c.m.sha256(v) for p, v in result.items()},
            "modules_sha256": {"invented.py": "f" * 64},
        }
    )
    return result


@pytest.fixture
def original(monkeypatch):
    documents = logical()
    saved = payload(documents)
    archive = FakeGitHub(c.m.DESTINATION)
    c.b.create(archive, d.REPORT_REF, saved, c.m.BOOTSTRAP)
    digest = c.m.sha256(u.canonical({p: c.m.sha256(v) for p, v in sorted(saved.items())}))
    monkeypatch.setattr(d, "BASELINE_BUNDLE", digest)
    archive.events.clear()
    return archive, documents, saved


def revised(documents):
    changed = dict(documents)
    result = json.loads(changed["result.json"])
    result.update(schema="rotation_stress_result_v2", source_commit=SOURCE)
    changed["result.json"] = u.canonical(result)
    changed["decisions.json"] = changed["decisions.json"].replace(b"0E-28", b"0.00")
    return changed


def test_exact_pinned_prior_report_and_declared_zero_text_change_only(original):
    archive, documents, saved = original
    assert d.load_original(archive) == saved
    report = d.compare_to_baseline(archive, revised(documents), SOURCE)
    assert report["semantics_match"] and report["input_bytes_exactly_equal"]
    assert report["status"].endswith("NOT_ACCEPTED")
    assert report["performance_admission"] is False
    assert report["logical_files"]["decisions.json"]["declared_change_counts"] == {
        "exact_zero_text_representation_only": 1
    }
    assert all(method == "GET" for method, _ in archive.events)


def test_even_sub_tolerance_money_change_blocks_new_report(original):
    archive, documents, _ = original
    changed = revised(documents)
    changed["result.json"] = changed["result.json"].replace(b"10000.125", b"10000.125000000001")
    report = d.compare_to_baseline(archive, changed, SOURCE)
    assert not report["semantics_match"]
    assert report["logical_files"]["result.json"]["numeric_difference_bins_not_tolerances"] == {
        "abs_le_1e-12": 1
    }


def test_input_decimal_spelling_is_not_normalized_or_waived(original):
    archive, documents, _ = original
    changed = revised(documents)
    changed["inputs.json"] = changed["inputs.json"].replace(b"101.2345", b"101.234500")
    report = d.compare_to_baseline(archive, changed, SOURCE)
    assert not report["semantics_match"] and not report["input_bytes_exactly_equal"]


@pytest.mark.parametrize(
    "name,path",
    [
        ("decisions.json", ("close_sensitivity", "evil", "maximum_target_weight_difference")),
        ("result.json", ("rotation", "cash")),
    ],
)
def test_zero_text_waiver_cannot_escape_the_reproduced_field(name, path):
    first, second = "0.00", "0E-28"
    for key in reversed(path):
        first, second = {key: first}, {key: second}
    assert not d.declared_changes_only(name, first, second, SOURCE)[0]


def test_computed_nonzero_or_numeric_token_spelling_change_is_not_waived():
    before = {"close_sensitivity": {"monthly": [{"maximum_target_weight_difference": "0.0100"}]}}
    after = copy.deepcopy(before)
    after["close_sensitivity"]["monthly"][0]["maximum_target_weight_difference"] = "0.01"
    assert not d.declared_changes_only("decisions.json", before, after, SOURCE)[0]
    assert not d.declared_changes_only(
        "result.json", {"cash": Decimal("0.0")}, {"cash": Decimal("0.00")}, SOURCE
    )[0]


def test_diagnostic_never_prints_leaf_values_or_arbitrary_key_names():
    secret, sensitive_key = "DO_NOT_PUBLISH_INVENTED_PRICE_OR_SECRET", "INVENTED_SENSITIVE_KEY"
    report = d.differences(
        {sensitive_key: secret, "cash": 100},
        {sensitive_key: "DIFFERENT_PRIVATE_VALUE", "cash": 100.01},
    )
    text = json.dumps(report)
    assert (
        secret not in text and sensitive_key not in text and "DIFFERENT_PRIVATE_VALUE" not in text
    )
    assert "100.01" not in text and "field_sha256:" in text


def test_real_shape_zero_tie_witness_emits_only_zero_spellings_and_research_dates():
    data = {
        "decisions": [
            {
                "signal_session": "2024-11-29",
                "targets": {"XLB": "0.15", "XLC": "0.2000000000000000000000000000"},
            }
        ],
        "close_sensitivity": {
            "monthly": [
                {
                    "signal_session": "2024-11-29",
                    "alternate_targets": {"XLB": "0.15", "XLC": "0.2000000000000000000000000000"},
                }
            ]
        },
    }
    report = d.zero_text_witness(data)
    assert report == {
        "monthly_equal_zero_ties_with_multiple_spellings": 1,
        "examples": [{"signal_session": "2024-11-29", "equal_zero_spellings": ["0.00", "0E-28"]}],
    }
    assert "0.200000" not in json.dumps(report)


def test_missing_or_wrong_report_pin_does_not_use_another_archive(original, monkeypatch):
    archive, _, _ = original
    monkeypatch.setattr(d, "BASELINE_BUNDLE", "0" * 64)
    with pytest.raises(ValueError, match="BUNDLE_CONFLICT"):
        d.load_original(archive)
    assert all(method == "GET" for method, _ in archive.events)


@pytest.mark.parametrize("body", [b'{"cash":1,"cash":2}', b'{"cash":NaN}'])
def test_ambiguous_json_or_nonfinite_numeric_values_reject(body):
    with pytest.raises(ValueError):
        d.decode(body)
