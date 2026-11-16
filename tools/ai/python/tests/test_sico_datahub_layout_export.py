"""Visible-window OASIS export contract and SKILL source boundaries."""

import json
from pathlib import Path

from sico_datahub import contracts
from sico_datahub.registry import default_registry

ROOT = Path(__file__).resolve().parents[4]
CONTRACT = ROOT / "tools/common/datahub/contracts/sico.cad.layout_export.v1.json"
SKILL = ROOT / "tools/ai/skill/AI_layout_export.il"
GEOMETRY = ROOT / "tools/ai/skill/AI_layout_export_geometry.il"


def test_visible_oasis_contract_is_registered_and_non_signoff():
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert value["name"] == "sico.cad.layout_export.v1"
    assert value["actions"]["visible_oasis"]["capability"] == (
        "aiLayoutExportVisibleOasis"
    )
    assert value["actions"]["visible_oasis"]["request"]["fields"] == [
        "window_ref",
        "output_path",
        "work_root",
        "layer_map",
        "region",
    ]
    assert value["actions"]["visible_oasis"]["response"]["schema"] == (
        "cad.layout_export.v1"
    )
    assert contracts.ACTIONS["sico.cad.layout_export.v1"] == ("visible_oasis",)
    spec = default_registry().action(
        "sico.cad.layout_export.v1", 1, "visible_oasis"
    )
    assert spec[1].response_schema == "cad.layout_export.v1"


def test_skill_exports_the_visible_fragment_without_strmout():
    text = SKILL.read_text(encoding="utf-8")
    geometry = GEOMETRY.read_text(encoding="utf-8")
    assert "procedure(aiLayoutExportVisibleOasis" in text
    assert "hiGetViewBBox" in text
    assert "dbCopyFig" in text
    assert "dbFlattenInst2" in geometry
    assert "dbLayerAnd" in geometry
    assert "oasisout" in text
    assert "visible_fragment" in text
    assert "connectivity_preserved" in text
    assert "lvs_eligible" in text
    assert ".sico-visible.json" in text
    assert "'pdkJsonFalse" in text
    assert "Visible-window sidecar already exists" in text
    assert "Visible-window sidecar could not be written" in text
    assert "@optional aleRegion" in text
    assert "region_source" in text
    assert "explicit_region" in text
    assert "aiLayoutExportInside(aleCvBox aleRegionBox)" in text
    assert "strmout" not in text.lower()
    assert "sico.cad.layout_export.v1" in text


def test_layout_export_is_part_of_the_generated_shared_skill():
    manifest = json.loads(
        (ROOT / "tools/utility/ai_shared_sources.json").read_text(encoding="utf-8")
    )
    assert "AI_layout_export_geometry.il" in manifest["sources"]
    assert "AI_layout_export.il" in manifest["sources"]
    generated = (ROOT / "tools/ai/skill++/AI_shared.ils").read_text(
        encoding="utf-8"
    )
    assert "aiLayoutExportVisibleOasis" in generated
    assert "aiLayoutExportClip" in generated
