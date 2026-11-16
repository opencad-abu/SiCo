"""Catalog-level integration: detail levels, relations and routing-style sections."""

from __future__ import annotations

import json
from pathlib import Path

from test_circuit_templates import make_catalog

from cadai.template_build import publish
from cadai.template_catalog import TemplateCatalog
from cadai.template_catalog import main as catalog_main
from cadai.template_project import LEVELS, project_record

CASE = Path(__file__).parent / "test_template_circuit_pg.py"


def reference_capture():
    """Reuse the PG fixture capture rows (two devices, VDD/VSS supply typing)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("pg_case_module", CASE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.capture_rows()


def build_tiers(tmp_path):
    root = tmp_path / ".cad/ai/reference/circuit-templates"
    root.mkdir(parents=True)
    make_catalog(root, reference_capture())
    catalog = TemplateCatalog(root)
    base_ref = catalog.query({"offset": 0, "limit": 1})["templates"][0]["template_ref"]
    base = catalog.get(base_ref)
    records = [project_record(base, level) for level in LEVELS]
    shards = root / "catalogs"
    shards.mkdir()
    publish(records, shards / "projections.sqlite3")
    return root, base


def test_detail_level_filter_selects_projection_only(tmp_path):
    root, base = build_tiers(tmp_path)
    catalog = TemplateCatalog(root)
    for level in LEVELS:
        page = catalog.query({"offset": 0, "limit": 10, "detail_level": level})
        assert page["returned"] == 1
        summary = page["templates"][0]
        assert summary["detail_level"] == level
        assert summary["template_ref"] != base["template_ref"]
    everything = catalog.query({"offset": 0, "limit": 10})
    assert everything["returned"] == 4  # base plus three projections
    levels = sorted(
        (summary.get("detail_level") or "base") for summary in everything["templates"]
    )
    assert levels == ["L1", "L2", "L3", "base"]


def test_relations_section_is_served_from_projection_and_computed_for_base(tmp_path):
    root, base = build_tiers(tmp_path)
    catalog = TemplateCatalog(root)
    l1 = catalog.query({"offset": 0, "limit": 10, "detail_level": "L1"})["templates"][0]
    stored = catalog.detail(
        {"template_ref": l1["template_ref"], "section": "relations", "entity": "all",
         "offset": 0, "limit": 100}
    )
    assert stored["metadata"]["schema"] == "cad.template.relations.v1"
    assert stored["total"] > 0
    assert {row["entity"] for row in stored["records"]} >= {"instances", "rows"}

    computed = catalog.detail(
        {"template_ref": base["template_ref"], "section": "routing_style", "entity": "nets",
         "offset": 0, "limit": 100}
    )
    assert computed["metadata"]["schema"] == "cad.template.routing_style.v1"
    assert all(row["entity"] == "nets" for row in computed["records"])
    assert computed["metadata"]["totals"]["segments"] >= 0


def test_parameters_section_only_exists_for_sized_records(tmp_path):
    root, base = build_tiers(tmp_path)
    catalog = TemplateCatalog(root)
    l3 = catalog.query({"offset": 0, "limit": 10, "detail_level": "L3"})["templates"][0]
    sized = catalog.detail(
        {"template_ref": l3["template_ref"], "section": "parameters", "entity": "all",
         "offset": 0, "limit": 100}
    )
    assert sized["metadata"]["schema"] == "cad.template.parameters.v1"
    l1 = catalog.query({"offset": 0, "limit": 10, "detail_level": "L1"})["templates"][0]
    missing = catalog.detail(
        {"template_ref": l1["template_ref"], "section": "parameters", "entity": "all",
         "offset": 0, "limit": 100}
    )
    assert missing["ok"] is False and missing["code"] == "asset_unavailable"


def test_relations_require_a_schematic_asset(tmp_path):
    root, base = build_tiers(tmp_path)
    catalog = TemplateCatalog(root)
    l1 = catalog.query({"offset": 0, "limit": 10, "detail_level": "L1"})["templates"][0]
    # L1 stores relations itself; recomputing from the L1 record has no raw geometry
    assert catalog.detail(
        {"template_ref": l1["template_ref"], "section": "relations", "entity": "all",
         "offset": 0, "limit": 10}
    )["total"] > 0
    absent = catalog.detail(
        {"template_ref": l1["template_ref"], "section": "schematic", "entity": "all",
         "offset": 0, "limit": 10}
    )
    assert absent["ok"] is False and absent["code"] == "asset_unavailable"


def test_project_cli_publishes_one_sanitized_level(tmp_path, capsys):
    root, base = build_tiers(tmp_path)
    output = tmp_path / "catalogs" / "projected-L1.sqlite3"
    output.parent.mkdir()
    catalog_main(
        [
            "project", "--legacy-sqlite",
            "--workspace",
            str(tmp_path),
            "--template-ref",
            base["template_ref"],
            "--level",
            "L1",
            "--output",
            str(output),
        ]
    )
    printed = capsys.readouterr().out
    summary = json.loads(printed)
    assert summary["detail_level"] == "L1"
    assert summary["base_template_ref"] == base["template_ref"]
    published = TemplateCatalog(root=tmp_path)
    records = published.query({"offset": 0, "limit": 10})["templates"]
    assert len(records) == 1 and records[0]["detail_level"] == "L1"
    record = published.get(records[0]["template_ref"])
    text = json.dumps(record, ensure_ascii=False)
    assert "value_skill" not in text and "library_path" not in text
    assert record["assets"] == {}
