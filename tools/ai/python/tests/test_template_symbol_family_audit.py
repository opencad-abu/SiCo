"""Automatic family selection must be unique and plan every pinned source."""

import json

from test_draw_symbol import gate_record

from cadai.template_build import publish
from cadai.template_symbol_family_audit import main as audit_main
from cadai.template_symbol_family_build import main as build_main


def test_audit_selects_every_pinned_source(tmp_path):
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
                        "cell_hints": ["inv"],
                        "roles": {"VDD": "power", "VSS": "ground"},
                    }
                ],
                "required_families": ["inv"],
            }
        )
    )
    registry = tmp_path / "families"
    assert (
        build_main(
            [
                "build",
                "--catalog",
                str(catalog),
                "--manifest",
                str(manifest),
                "--output",
                str(registry),
            ]
        )
        == 0
    )
    report_path = tmp_path / "audit.json"
    assert (
        audit_main(
            [
                "--catalog",
                str(catalog),
                "--manifest",
                str(manifest),
                "--registry",
                str(registry),
                "--report",
                str(report_path),
            ]
        )
        == 0
    )
    report = json.loads(report_path.read_text())
    assert report["passed"]
    assert report["counts"]["selected"] == 1
    assert report["counts"]["required_family_covered"] == 1
    assert report["counts"]["coverage_target_met"]
    assert report["missing_required_families"] == []
    assert report["items"][0]["selected_family"] == "inv"
    assert report["items"][0]["preview_digest"]


def test_audit_reports_a_missing_hint_and_returns_nonzero(tmp_path):
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
                        "cell_hints": ["unrelated"],
                    }
                ],
            }
        )
    )
    registry = tmp_path / "families"
    assert (
        build_main(
            [
                "build",
                "--catalog",
                str(catalog),
                "--manifest",
                str(manifest),
                "--output",
                str(registry),
            ]
        )
        == 0
    )
    assert (
        audit_main(
            [
                "--catalog",
                str(catalog),
                "--manifest",
                str(manifest),
                "--registry",
                str(registry),
            ]
        )
        == 1
    )
