"""Device enrichment tests: tiers, effective ranges, categories, readiness (no Virtuoso)."""

import pytest

from cadai import pdk_enrich


def section(status="complete", items=None):
    items = items or []
    return {"status": status, "items": items, "count": len(items), "truncated": False,
            "issues": [], "source": "test"}


def device():
    params = [
        {"name": name, "default": {"status": "known", "value": value}}
        for name, value in (("model", "n09_ckt"), ("w", "1u"), ("l", "0.03u"),
                            ("dummy", "x"), ("simTotal", "0"), ("nf", "1"))
    ]
    ports = [{"name": name, "direction": "inputOutput"} for name in ("D", "G", "S", "B")]
    envelope = {"status": "known", "type": "string"}
    simulators = [{"name": "spectre", "raw": {
        "instParameters": {"status": "known", "type": "list",
                           "value": [{**envelope, "value": name} for name in ("w", "l", "nf")]}}}]
    return {"device_ref": "device:libx/n09_ckt/symbol",
            "target": {"library": "libx", "cell": "n09_ckt", "view": "symbol"},
            "classification": {"kind": "unknown", "family": "unknown", "status": "unknown"},
            "identity": section(items=[{"target": {"library": "libx", "cell": "n09_ckt"}}]),
            "ports": section(items=ports), "parameters": section(items=params),
            "geometry": section(items=[]), "simulators": section(items=simulators),
            "callbacks": section(items=[]), "project_models": section("not_requested"),
            "master_state": {}}


LIMITS = {
    "wmin": {"value": 9e-8, "as_entered": "9e-008", "file": "x_spe.mdl", "line": 110},
    "wmax": {"value": 2.7e-6, "as_entered": "2.7e-006", "file": "x_spe.mdl", "line": 110},
    "lmin": {"value": 2.7e-8, "as_entered": "2.7e-008", "file": "x_spe.mdl", "line": 110},
    "lmax": {"value": 1.8e-6, "as_entered": "1.8e-006", "file": "x_spe.mdl", "line": 110},
}
DOCUMENTED = {"limits": {"l": {"min": 3e-8, "as_entered": "0.03um"}},
              "file": "x_readme_spe.txt", "line": 190, "headers": ["MOSFET type", "name", "Lmin", "Wmin"]}


def resolution():
    return {"queried": "n09_ckt", "resolved": "n09_ckt", "status": "found", "basis": "model_name",
            "decks": [{"simulator": "spectre", "kind": "subckt", "file": "x_spe.mdl", "line": 110}],
            "terminals": ["d", "g", "s", "b"], "instance_args": ["w", "l", "sa"],
            "limits": LIMITS, "documented": DOCUMENTED, "readme": []}


INDEX = {"available": True, "root": "/install", "digest": "indexdigest", "document_entries": 1,
         "rule_decks": [], "documents": [{"relative": "docs/SPICE_model/user.pdf"}]}


@pytest.mark.parametrize("name,interfaces,decks,tier", [
    ("w", {"w"}, set(), "interface"), ("model", set(), set(), "interface"),
    ("seg", set(), set(), "interface"), ("sa", set(), {"sa"}, "interface"),
    ("simTotal", set(), set(), "derived"), ("effW", set(), set(), "derived"),
    ("calculatedParam", set(), set(), "interface"),
    ("segW", set(), set(), "interface"), ("segL", set(), set(), "interface"),
    ("segments", set(), set(), "interface"), ("segR", set(), set(), "derived"),
    ("dummy", set(), set(), "auxiliary"), ("dfm", set(), set(), "auxiliary"),
])
def test_parameter_tier_uses_sourced_evidence_first(name, interfaces, decks, tier):
    assert pdk_enrich.parameter_tier(name, interfaces, decks) == tier


def test_range_block_merges_model_bin_with_documented_minimum():
    model_only = pdk_enrich.range_block(LIMITS, None, "w")
    assert model_only["status"] == "partial" and model_only["scope"] == "effective"
    assert model_only["min"] == pytest.approx(9e-8) and model_only["max"] == pytest.approx(2.7e-6)
    assert model_only["effective_from"] == ["model_bin"]
    merged = pdk_enrich.range_block(LIMITS, DOCUMENTED, "l")
    assert merged["status"] == "complete"
    assert merged["min"] == pytest.approx(3e-8) and merged["max"] == pytest.approx(1.8e-6)
    assert merged["effective_from"] == ["model_bin", "design_rule"]
    assert merged["scopes"]["design_rule"]["as_entered"] == "0.03um"
    assert merged["source"]["kind"] == "model_deck"
    unknown = pdk_enrich.range_block({}, None, "w")
    assert unknown["status"] == "unknown" and unknown["min"] is None and unknown["max"] is None
    # `fingers`/`m` map onto the nf/mr decks keys.
    aliased = pdk_enrich.range_block({"nfmin": {"value": 1.0, "as_entered": "1", "file": "f",
                                                "line": 1},
                                      "nfmax": {"value": 64.0, "as_entered": "64", "file": "f",
                                                "line": 1}}, None, "fingers")
    assert aliased["min"] == 1.0 and aliased["max"] == 64.0


def test_enrich_marks_tiers_ranges_categories_and_readiness():
    categories = pdk_enrich.device_categories(
        {"cells": {"n09_ckt": ["mos", "nwell"]}, "status": "complete"}, "n09_ckt")
    result = pdk_enrich.enrich(device(), resolution(), categories, INDEX)
    parameters = {item["name"]: item for item in result["parameters"]["items"]}
    assert parameters["w"]["tier"] == "interface"
    assert parameters["w"]["tier_reason"] == ["sim_info", "model_deck", "core_set"]
    assert parameters["w"]["range"]["min"] == pytest.approx(9e-8)
    assert parameters["w"]["default_in_range"] is True
    assert parameters["l"]["range"]["min"] == pytest.approx(3e-8)
    assert parameters["l"]["default_in_range"] is True
    assert parameters["dummy"]["tier"] == "auxiliary"
    assert parameters["simTotal"]["tier"] == "derived"
    assert result["parameter_tiers"]["counts"]["interface"] == 4
    assert categories["primary"] == "mos" and categories["ambiguous"] is True
    assert result["categories"]["all"] == ["mos", "nwell"]
    # A MOS category cannot decide nmos/pmos, so the kind stays explicitly unknown.
    assert result["classification"]["kind"] == "unknown"
    assert result["port_crosscheck"]["same_set"] is True and result["port_crosscheck"]["same_order"] is True
    assert result["port_crosscheck"]["candidate_map"] == []
    assert result["model_resolution"]["status"] == "found"
    assert result["model_decks"][0]["file"] == "x_spe.mdl"
    assert result["limits"]["source_root"] == "/install"
    assert result["limits"]["index_digest"] == "indexdigest"
    assert result["file_layer"]["available"] is True
    readiness = result["readiness"]
    assert readiness["identity"] == "ok" and readiness["ports"] == "ok"
    assert readiness["pins"] == "missing" and readiness["ranges"] == "ok"
    assert readiness["simulation"] == "ok" and readiness["docs"] == "indexed"
    assert readiness["overall"] == "partial"  # pins are not published yet


def test_category_registry_fills_the_kind_only_when_it_can_decide():
    for category, kind in (("resistors", "resistor"), ("diodes", "diode"),
                           ("capacitor", "capacitor")):
        registry = pdk_enrich.device_categories({"cells": {"d": [category]}, "status": "complete"}, "d")
        result = pdk_enrich.enrich(device(), resolution(), registry, INDEX)
        assert result["classification"]["kind"] == kind
        assert result["classification"]["status"] == "derived_from_category"
        assert result["classification"]["source"]["inputs"] == {"category": category}


def test_enrich_keeps_unknowns_explicit_without_a_file_layer():
    result = pdk_enrich.enrich(device(),
                               {"queried": "n09_ckt", "resolved": None, "status": "decks_unavailable",
                                "basis": None, "decks": [], "terminals": [], "instance_args": [],
                                "limits": {}, "documented": None, "readme": []},
                               pdk_enrich.device_categories({}, "n09_ckt"),
                               {"available": False, "root": None, "digest": None, "models": {}})
    parameters = {item["name"]: item for item in result["parameters"]["items"]}
    assert parameters["w"]["range"]["status"] == "unknown"
    assert parameters["w"]["default_in_range"] == "unknown"
    assert result["model_resolution"]["status"] == "decks_unavailable"
    assert result["readiness"]["identity"] == "ok"
    # W/L exist but no range is published: readiness stays honest instead of "ok".
    assert result["readiness"]["ranges"] == "missing"


def test_categories_prefer_the_runtime_registry_and_keep_the_cross_check():
    file_registry = {"cells": {"cell_a": ["mos"], "cell_b": ["resistors"]},
                     "categories": {"mos": {"cells": {"cell_a": {"line": 1}}},
                                    "resistors": {"cells": {"cell_b": {"line": 1}}}},
                     "files": [{"file": "/install/libx/mos.Cat", "category": "mos", "entries": 1}]}
    runtime = {"status": "complete", "issues": [],
               "items": [{"name": "mos", "members": ["cell_a", "cell_c"], "source": "ddCat"}]}
    registry = pdk_enrich.effective_categories(runtime, "libx", file_registry)
    # ddCat wins per category name it exposes; `.Cat` supplies the names it does not.
    assert registry["cells"] == {"cell_a": ["mos"], "cell_c": ["mos"], "cell_b": ["resistors"]}
    assert registry["ddcat_cells"] == ["cell_a", "cell_c"]
    assert registry["file_fallback_cells"] == ["cell_b"]
    assert registry["category_source"] == {"mos": "ddCat_registry",
                                           "resistors": "library_category_files"}
    assert registry["source"]["kind"] == "ddCat_registry"
    assert registry["status"] == "complete" and registry["issues"] == []
    assert registry["cross_check"] == {"registry_cells": 2, "file_cells": 2,
                                       "registry_only": ["cell_c"], "file_only": ["cell_b"],
                                       "consistent": False}
    assert pdk_enrich.device_categories(registry, "cell_a")["source"]["kind"] == "ddCat_registry"
    fallback_cell = pdk_enrich.device_categories(registry, "cell_b")
    assert fallback_cell["source"]["kind"] == "library_category_files"
    assert fallback_cell["primary"] == "resistors"
    assert fallback_cell["source"]["category_authority"] == {
        "resistors": "library_category_files"}
    fallback = pdk_enrich.effective_categories(
        {"status": "unavailable", "items": [], "issues": ["category_capture_failed"]}, "libx",
        file_registry)
    assert fallback["cells"] == {"cell_a": ["mos"], "cell_b": ["resistors"]}
    assert fallback["source"]["kind"] == "library_category_files"
    assert fallback["fallback"] == "runtime_registry_empty"


def test_qualification_follows_the_d4_gate():
    assert pdk_enrich.qualification({"status": "found"}) == (True, None)
    assert pdk_enrich.qualification({"status": "decks_unavailable"}) == (True, "model_decks_unavailable")
    assert pdk_enrich.qualification({"status": "decks_truncated"}) == (True, "model_decks_truncated")
    assert pdk_enrich.qualification({"status": "not_found_in_shipped_decks"}) == (False, "model_not_in_decks")
