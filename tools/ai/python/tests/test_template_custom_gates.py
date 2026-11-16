"""Curated gate publication and user/company style precedence."""

import hashlib
import importlib.util
import json
from pathlib import Path

from test_template_library_tiers import install, query, record
from test_template_library_tiers import roots as tier_roots

from cadai.template_catalog import TemplateCatalog

roots = tier_roots
ROOT = Path(__file__).resolve().parents[2] / "reference/circuit_templates"
spec = importlib.util.spec_from_file_location(
    "gate_builder", ROOT / "custom_gates/build_catalog.py"
)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def test_gate_selection_excludes_drive_and_alternate_symbol_duplicates():
    def target(cell, family="INV", view="symbol"):
        return {"library": "gsclib045", "cell": cell, "family": family, "symbol_view": view}

    selected, skipped = builder.select_targets(
        [
            target("INVX1"),
            target("INVX1", view="symbolPT"),
            target("INVX4"),
            target("CLKINVX1", "CLKINV"),
            target("DFFRX1", "DFFR"),
            target("DFFX1", "DFF"),
        ]
    )
    assert [t["cell"] for t in selected] == ["INVX1", "DFFRX1", "DFFX1"]
    assert len(skipped) == 3


def test_builtin_custom_gate_manifest_preserves_outlines_without_drive_duplicates():
    manifest = json.loads((ROOT / "custom_gates/SOURCES.json").read_text())
    pub = manifest["publication"]
    assert pub["templates"] == 69
    assert hashlib.sha256((ROOT / pub["catalog"]).read_bytes()).hexdigest() == pub["sha256"]
    assert manifest["assets"] == {"schematic": 69, "symbol": 69, "layout": 69}
    items = manifest["templates"]
    assert len({(t["library"], t["family"]) for t in items}) == 69
    inv = next(t for t in items if t["family"] == "INV")
    assert inv["representative_cell"] == "INVX1" and "INVX4" in inv["available_drive_cells"]
    stored = TemplateCatalog(ROOT).get(inv["template_ref"])
    assert stored["summary"]["reference_set"] == "custom_gates45"
    assert stored["summary"]["counts"]["device_kinds"] == {"nmos": 1, "pmos": 1}
    assert inv["shape_reference"]["figure_types"]["ellipse"] >= 1
    assert not inv["shape_reference"]["automatic_style_recreation"]
    assert inv["layout_coverage"]["binding_status"] == "binding_unavailable"


def test_user_and_company_style_rank_before_builtin_even_with_different_names(roots):
    workspace, private, project, builtin = roots
    for root, variant, cell in [
        (private, "user", "ZZ_INV_user"),
        (project, "company", "MM_INV_company"),
        (builtin, "builtin", "AA_INV_builtin"),
    ]:
        item = record(variant)
        item["source"]["cell"] = item["summary"]["cell"] = cell
        install(root, item)
    output = query(TemplateCatalog(workspace=workspace), query="inverter")
    assert [t["cell"] for t in output["templates"]] == [
        "ZZ_INV_user",
        "MM_INV_company",
        "AA_INV_builtin",
    ]
    assert [t["catalog_origin"]["tier"] for t in output["templates"]] == [
        "private",
        "project",
        "builtin",
    ]
