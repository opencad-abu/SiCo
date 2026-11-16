"""Retained legacy captures keep their facts, gaps and reference through migration."""

from pathlib import Path

from test_circuit_templates import legacy_rows, write_capture

from cadai.template_build import make_template, publish
from cadai.template_capture import read_capture
from cadai.template_catalog import TemplateCatalog
from cadai.template_standard import migrate
from cadai.template_standard.reader import snapshot
from cadai.template_standard.replay import engine_record, replay

AT = "2026-10-09T00:00:00+00:00"


def legacy_fixture(tmp_path):
    capture_path = write_capture(tmp_path, legacy_rows())
    raw = capture_path.read_bytes()
    record = make_template(
        read_capture(capture_path), "digital", {"origin": "fixture", "version": "1"}
    )
    publish([record], tmp_path / "catalog.sqlite3")
    return raw, record


def test_retained_capture_keeps_unknown_use_and_legacy_reference(tmp_path):
    raw, record = legacy_fixture(tmp_path)
    converted = migrate.convert(
        raw,
        record["template_ref"],
        category=record["category"],
        provenance=record["provenance"],
        at=AT,
    )
    entry = converted["tables"]["template"]["items"]["main"]
    assert entry["use"] == {"reference": "unknown", "reuse": "unknown"}
    assert entry["derived_from"] == [record["template_ref"]]
    assert converted["docs"]["collection"]["legacy_refs"] == [record["template_ref"]]
    assert entry["template_ref"].startswith("ctpl_")
    # No live library/cell is invented, so the request carries no authorization.
    assert converted["request"]["scope"]["options"] == {
        "legacy_ref": record["template_ref"]
    }


def test_migrated_package_reads_back_and_replays_its_pinned_evidence(tmp_path):
    raw, record = legacy_fixture(tmp_path)
    converted = migrate.convert(
        raw,
        record["template_ref"],
        category=record["category"],
        provenance=record["provenance"],
        at=AT,
    )
    root, evidence = tmp_path / "standard", tmp_path / "evidence"
    migrate.install(root, evidence, converted)
    items, _ = snapshot(TemplateCatalog(root=root))
    ref = migrate.template_ref(converted)
    assert list(items) == [ref]
    item = items[ref]
    assert item["documents"]["collection"]["legacy_refs"] == [record["template_ref"]]
    checks = item["documents"]["qualification"]["checks"]
    assert checks["topology-v1"]["result"] == "unknown"
    assert checks["whole-reuse-v1"]["result"] == "unknown"
    replay_record, capture, retained, scope = replay(item)
    assert capture["sha256"] == converted["capture"]["sha256"]
    assert retained == raw
    assert scope["options"]["legacy_ref"] == record["template_ref"]
    assert replay_record["source"]["cell"] == record["source"]["cell"]
    engine = engine_record(item)
    assert engine["standard_use"]["reference"] == "unknown"


def test_migration_identity_is_independent_of_the_output_location(tmp_path):
    raw, record = legacy_fixture(tmp_path)
    first = migrate.convert(
        raw,
        record["template_ref"],
        category=record["category"],
        provenance=record["provenance"],
        at=AT,
    )
    second = migrate.convert(
        raw,
        record["template_ref"],
        category=record["category"],
        provenance=record["provenance"],
        at=AT,
    )
    assert migrate.template_ref(first) == migrate.template_ref(second)
    other_evidence = tmp_path / "elsewhere"
    migrate.install(tmp_path / "root-a", tmp_path / "evidence-a", first)
    migrate.install(tmp_path / "root-b", other_evidence, second)
    left, _ = snapshot(TemplateCatalog(root=tmp_path / "root-a"))
    right, _ = snapshot(TemplateCatalog(root=tmp_path / "root-b"))
    assert list(left) == list(right)
    assert Path(evidence_root(left)) != Path(evidence_root(right))


def evidence_root(items):
    item = next(iter(items.values()))
    return item["roots"]["private"]
