"""End-to-end enrichment: D4 exclusions, tiers, ranges and quality on a synthetic install tree."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from cadai.pdk_preparation import PdkPreparation
from pdk_fixture import Bridge, raw
from test_pdk_preparation import context_with_pdk, finish


def build_install(tmp_path):
    root = tmp_path / "install"
    lib = root / "pdk_a"
    (lib / "cell0" / "symbol").mkdir(parents=True)
    (lib / "cell_extra" / "layout").mkdir(parents=True)
    (lib / "mos.Cat").write_text('pdk_a/cell0 type="cell"\n')
    models = root / "models" / "spectre"
    models.mkdir(parents=True)
    (models / "a_spe.mdl").write_text(
        "inline subckt GOODMODEL (d g s b)\n"
        "parameters lmin=3e-8 lmax=1.8e-6 wmin=9e-8 wmax=2.7e-6\n"
        "ends GOODMODEL\n")
    return root


def model_param(value):
    return {"name": raw("model"), "paramType": raw("string"), "type": raw("cellData"),
            "defValue": raw(value), "editable": raw("cdfgData->x->value"), "display": raw("t"),
            "callback": raw("error(\"must not execute\")"), "parseAsNumber": raw("yes"),
            "parseAsCEL": raw(None), "units": raw("lengthMetric")}


def patched(bridge, model_for):
    capture = bridge.capture

    def wrapper(op, target):
        result = capture(op, target)
        if op == "categories":
            result["data"]["items"][0]["members"] = ["cell0", "cell1", "cell2",
                                                     "cell_extra", "cell_missing"]
        elif op == "device":
            result["data"]["cdf"]["parameters"]["items"].append(
                model_param(model_for(target["cell"])))
        elif op == "directory":
            for record in result["data"]["items"]:
                record["cdf_summary"]["names"] = ["model", "w"]
        return result

    return wrapper


def test_model_resolution_does_not_discard_observed_cells(tmp_path, monkeypatch):
    root = build_install(tmp_path)
    bridge = Bridge()
    context_with_pdk(bridge, ("pdk_a",))
    bridge.ctx["libraries"][0]["resolved_path"] = str(root / "pdk_a")
    monkeypatch.setattr(bridge, "capture", patched(
        bridge, lambda cell: "BADMODEL" if cell == "cell1" else "GOODMODEL"))
    service = PdkPreparation(bridge, workspace=tmp_path, environment={})
    job = service.prepare({"library": "pdk_a", "choice_confirmed": True})
    done = finish(service, job)
    assert done["status"] == "ready"
    assert done["coverage"]["device_count"] == 3
    assert done["coverage"]["categories"] == {"mos": 3}
    assert done["coverage"]["category_missing"] == 0
    assert done["coverage"]["model_status"] == {"found": 2, "not_found_in_shipped_decks": 1}
    assert done["coverage"]["limits_published"] == 2
    assert done["coverage"]["readiness"] == {"partial": 3}  # single-port synthetic symbol
    quality = done["quality"]
    assert quality["included_devices"] == 3 and quality["excluded_count"] == 2
    assert quality["excluded_reasons"] == {"no_symbol_view": 1,
                                           "registry_stale": 1}
    assert {(row["cell"], row["reason"]) for row in quality["excluded_devices"]} == {
        ("cell_extra", "no_symbol_view"),
        ("cell_missing", "registry_stale")}
    # The raw registry and the per-cell exclusion map never leak into the tool result.
    assert "categories" not in done and "excluded" not in done
    manifest = json.loads(Path(done["path"]).read_text())
    assert set(manifest["payload"]["devices"]) == {"cell0", "cell1", "cell2"}
    assert manifest["payload"]["excluded"] == {}
    assert manifest["payload"]["quality"]["excluded_count"] == 2
    devices = [json.loads(path.read_text())["device"]
               for path in (service.data.root / "devices").glob("*.json")]
    assert len(devices) == 3
    for device in devices:
        if device["target"]["cell"] == "cell1":
            assert device["model_resolution"]["status"] == "not_found_in_shipped_decks"
            continue
        assert device["categories"]["all"] == ["mos"]
        assert device["model_resolution"]["status"] == "found"
        assert device["limits"]["model_deck"]["wmin"]["as_entered"] == "9e-8"
        assert device["file_layer"]["available"] is True
        parameters = {item["name"]: item for item in device["parameters"]["items"]}
        assert {name: item["tier"] for name, item in parameters.items()} == {
            "model": "interface", "w": "interface"}
        assert parameters["w"]["range"]["min"] == pytest.approx(9e-8)
        assert parameters["w"]["range"]["max"] == pytest.approx(2.7e-6)
        assert parameters["w"]["range"]["effective_from"] == ["model_bin"]
        assert parameters["w"]["default_in_range"] == "unknown"  # empty synthetic default
    # The published database validates and is reused with its exclusion report intact.
    loaded = PdkPreparation(bridge, workspace=tmp_path, environment={})
    ready = loaded.prepare({"library": "pdk_a"})
    assert ready["status"] == "ready"
    assert ready["quality"]["excluded_count"] == 2
    assert ready["coverage"]["device_count"] == 3


def test_recollection_keeps_device_digests_for_unchanged_content(tmp_path, monkeypatch):
    """§2.6/A5: observation timestamps stay out of the device fingerprint."""
    root = build_install(tmp_path)
    bridge = Bridge()
    context_with_pdk(bridge, ("pdk_a",))
    bridge.ctx["libraries"][0]["resolved_path"] = str(root / "pdk_a")
    monkeypatch.setattr(bridge, "capture", patched(bridge, lambda cell: "GOODMODEL"))
    layouts = []
    for run in ("run1", "run2"):
        workspace = tmp_path / run
        workspace.mkdir()
        service = PdkPreparation(bridge, workspace=workspace, environment={})
        done = finish(service, service.prepare({"library": "pdk_a", "choice_confirmed": True}))
        assert done["status"] == "ready"
        layouts.append((sorted(path.name for path in (service.data.root / "devices").glob("*.json")),
                        done["coverage"]))
    assert layouts[0][0] == layouts[1][0]  # identical content -> identical device digests
    assert layouts[0][1] == layouts[1][1]
