"""Manifest-driven symbol-family registry builds must be pinned and reproducible."""

import json

import pytest
from test_draw_symbol import gate_record

from cadai.template_build import publish
from cadai.template_schema import TemplateError
from cadai.template_symbol_family_build import main as build_main
from cadai.template_symbol_family_registry import read_registry


def test_registry_manifest_build_is_reproducible_and_reports_coverage(tmp_path):
    record = gate_record("INVX1")
    catalog = tmp_path / "catalog.sqlite3"
    publish([record], catalog)
    manifest = tmp_path / "families.json"
    manifest.write_text(
        json.dumps(
            {
                "format": "sico.symbol.family.build.v1",
                "families": [
                    {
                        "family_id": "inv",
                        "template_ref": record["template_ref"],
                        "library": "gsclib045",
                        "cell": "INVX1",
                        "cell_hints": ["inv"],
                        "roles": {"VDD": "power", "VSS": "ground"},
                        "pg_layout": "center",
                        "side_overrides": {"A": "left"},
                    }
                ],
                "required_families": ["inv", "resistor"],
            }
        )
    )
    first, second = tmp_path / "first", tmp_path / "second"
    for output in (first, second):
        assert (
            build_main(
                [
                    "build",
                    "--catalog",
                    str(catalog),
                    "--manifest",
                    str(manifest),
                    "--output",
                    str(output),
                    "--report",
                    str(output / "report.json"),
                ]
            )
            == 0
        )
    assert {p.name: p.read_bytes() for p in first.glob("*.json") if p.name != "report.json"} == {
        p.name: p.read_bytes() for p in second.glob("*.json") if p.name != "report.json"
    }
    families, hints = read_registry(first)
    assert set(families) == {"inv"} and hints == {"inv": ["inv"]}
    assert families["inv"]["pg_layout"] == "center"
    report = json.loads((first / "report.json").read_text())
    assert report["coverage"]["family_count"] == 1
    assert report["coverage"]["source_count"] == 1
    assert report["coverage"]["missing_families"] == ["resistor"]
    assert report["families"][0]["sources"][0]["template_ref"] == record["template_ref"]
    manifest.write_text(manifest.read_text().replace('"VDD"', '"UNKNOWN"'))
    with pytest.raises(TemplateError, match="roles reference"):
        build_main(
            [
                "build",
                "--catalog",
                str(catalog),
                "--manifest",
                str(manifest),
                "--output",
                str(tmp_path / "third"),
            ]
        )


def test_registry_manifest_requires_the_selected_catalog(tmp_path):
    selected = gate_record("INVX1")
    shard_record = gate_record("NAND2X1")
    publish([selected], tmp_path / "catalog.sqlite3")
    publish([shard_record], tmp_path / "catalogs" / "extra.sqlite3")
    manifest = tmp_path / "families.json"
    manifest.write_text(
        json.dumps(
            {
                "format": "sico.symbol.family.build.v1",
                "families": [
                    {
                        "family_id": "nand2",
                        "template_ref": shard_record["template_ref"],
                        "cell_hints": ["nand2"],
                    }
                ],
            }
        )
    )
    with pytest.raises(TemplateError, match="absent from the selected catalog"):
        build_main(
            [
                "build",
                "--catalog",
                str(tmp_path / "catalog.sqlite3"),
                "--manifest",
                str(manifest),
                "--output",
                str(tmp_path / "out"),
            ]
        )
