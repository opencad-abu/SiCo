"""Necessary-condition indexes are bounded hints and fail closed."""

import json

from test_template_library_tiers import record

from cadai.template_index import (
    build_index,
    load_index,
    necessary_candidates,
    validate_index_records,
)


def test_index_binds_snapshot_and_keeps_provenance(tmp_path):
    first = record("one")
    second = record("two")
    snapshot = {"snapshot_ref": "snp_test", "matcher_version": "matcher.v1"}
    path = tmp_path / "index.json"
    index = build_index(snapshot, [first, second], path)
    loaded, reason = load_index(path, "snp_test")
    assert reason is None
    assert loaded == index
    assert necessary_candidates(loaded, min_devices=0) == sorted(
        [first["template_ref"], second["template_ref"]]
    )


def test_index_uses_only_necessary_device_counts_and_falls_back_on_tamper(tmp_path):
    item = record("counts")
    snapshot = {"snapshot_ref": "snp_counts"}
    path = tmp_path / "index.json"
    index = build_index(snapshot, [item], path)
    assert necessary_candidates(index, device_kinds={"nmos": 999}) == []
    value = json.loads(path.read_text())
    value["rows"][0]["device_count"] += 1
    path.write_text(json.dumps(value))
    loaded, reason = load_index(path, "snp_counts")
    assert loaded is None and reason == "index_fallback"


def test_index_snapshot_mismatch_is_a_fallback(tmp_path):
    item = record("snapshot")
    path = tmp_path / "index.json"
    build_index({"snapshot_ref": "snp_a"}, [item], path)
    loaded, reason = load_index(path, "snp_b")
    assert loaded is None and reason == "index_fallback"


def test_core_index_counts_only_required_devices_and_detects_record_drift(tmp_path):
    item = record("required")
    item["schema_version"] = "cad.circuit.template.v3"
    item["reuse_contract"] = {"core_devices": [item["topology"]["devices"][0]["id"]]}
    snapshot = {"snapshot_ref": "snp_core"}
    path = tmp_path / "index.json"
    index = build_index(snapshot, [item], path)
    assert index["rows"][0]["required_device_count"] == 1
    assert necessary_candidates(
        index,
        available_device_kinds={item["topology"]["devices"][0]["kind"]: 1},
        count_field="required_device_count",
        kind_field="required_device_kinds",
    ) == [item["template_ref"]]
    changed = dict(item)
    changed["provenance"] = {"changed": True}
    assert validate_index_records(index, [item])
    assert not validate_index_records(index, [changed])
