"""Collect every symbol/CDF in an isolated native library into the workspace database."""

import json
from copy import deepcopy
from pathlib import Path

from cadai.pdk_preparation import PdkPreparation
from test_circuit_library_native import ROOT, native
from test_pdk_preparation import finish


def test_native_directory_and_full_details_publish_without_callbacks(tmp_path):
    cells = ["Device" + str(index) for index in range(5)]
    native(tmp_path, [
        f'load({json.dumps(str(ROOT / "skill" / name))})'
        for name in ("AI_pdk_core.il", "AI_pdk_init.il", "AI_pdk_cdf.il", "AI_pdk_db.il",
                     "AI_pdk_capture.il")
    ] + [
        f'ddCreateLib("SicoTest" {json.dumps(str(tmp_path / "SicoTest"))})',
        'aiWriteTextFile("SicoTest/libInit.il" "t\\n")',
        'tf=techOpenTechFile("SicoTest" "tech.db" "w") techSaveTechFile(tf)',
        'callbackRan=nil',
        'foreach(cell list("Device0" "Device1" "Device2" "Device3" "Device4") '
        'v=dbOpenCellViewByType("SicoTest" cell "symbol" "schematicSymbol" "a") '
        'n=dbCreateNet(v "P") term=dbCreateTerm(n "P" "inputOutput") '
        'fig=dbCreateRect(v list("pin" "drawing") list(0:0 0.125:0.125)) '
        'dbCreatePin(n fig nil term) dbSave(v) dbClose(v) '
        'c=cdfCreateBaseCellCDF(ddGetObj("SicoTest" cell)) '
        'unless(cdfCreateParam(c ?name "w" ?type "string" ?prompt "Width" ?defValue "1u" '
        '?units "lengthMetric" ?parseAsNumber "yes" ?callback "callbackRan=t") '
        'error("Test CDF parameter creation failed")) cdfSaveCDF(c))',
        'before=dbGetOpenCellViews()',
        'aiPdkCapture("collect" "session:collection" "libraries" nil nil nil)',
        'aiWriteTextFile("libraries.json" aiPdkTransfers["collect"]) aiPdkDrop("collect")',
        'aiPdkCapture("collect" "session:collection" "categories" "SicoTest" nil nil)',
        'aiWriteTextFile("categories.json" aiPdkTransfers["collect"]) aiPdkDrop("collect")',
        'aiPdkCapture("collect" "session:collection" "directory" "SicoTest" nil "symbol")',
        'aiWriteTextFile("directory.json" aiPdkTransfers["collect"]) aiPdkDrop("collect")',
        'foreach(cell list("Device0" "Device1" "Device2" "Device3" "Device4") '
        'aiPdkCapture("collect" "session:collection" "device" "SicoTest" cell "symbol") '
        'aiWriteTextFile(strcat(cell ".json") aiPdkTransfers["collect"]) aiPdkDrop("collect"))',
        'unless(equal(before dbGetOpenCellViews()) error("Collector leaked a database reference"))',
        'when(callbackRan error("Collector executed a callback"))',
        'unless(aiPdkTransferCount==0 error("Collector leaked a transfer"))',
    ], virtuoso=True)
    captures = {key: json.loads((tmp_path / (key + ".json")).read_text())
                for key in ["libraries", "categories", "directory", *cells]}

    class CapturedBridge:
        def __init__(self):
            self.calls = []

        def capture(self, operation, target):
            self.calls.append((operation, deepcopy(target)))
            return deepcopy(captures[target["cell"] if operation == "device" else operation])

    bridge = CapturedBridge()
    service = PdkPreparation(bridge, workspace=tmp_path, environment={})
    job = service.prepare({"library": "SicoTest"})
    first = service.advance({k: job[k] for k in ("collection_ref", "next_batch")})
    assert first["status"] == "collecting" and first["detailed_devices"] == 4
    done = finish(service, first)
    assert done["status"] == "ready" and done["coverage"]["device_count"] == 5
    manifest = json.loads(Path(done["path"]).read_text())
    assert set(manifest["payload"]["devices"]) == set(cells)
    for item in manifest["payload"]["devices"].values():
        device = json.loads((service.data.root / "devices" / (item["digest"] + ".json")).read_text())["device"]
        assert device["parameters"]["items"][0]["name"] == "w"
        assert device["ports"]["items"][0]["name"] == "P"
        assert device["geometry"]["count"] == 1
        assert device["callbacks"]["items"][0]["presence"] == "present"
        assert device["callbacks"]["items"][0]["execution_policy"] == "never_execute_during_discovery"
    assert sum(op == "directory" for op, _ in bridge.calls) == 1
    assert sum(op == "device" for op, _ in bridge.calls) == 5
    cached = PdkPreparation(bridge, workspace=tmp_path, environment={})
    before = len(bridge.calls)
    assert cached.prepare({"library": "SicoTest"})["status"] == "ready"
    assert all(op == "libraries" for op, _ in bridge.calls[before:])


def test_native_collection_retains_model_missing_cells(tmp_path):
    """Model-deck lookup cannot discard a symbol or grant device usage."""
    cells = ["PROBEGOOD", "PROBEBAD"]
    native(tmp_path, [
        f'load({json.dumps(str(ROOT / "skill" / name))})'
        for name in ("AI_pdk_core.il", "AI_pdk_init.il", "AI_pdk_cdf.il", "AI_pdk_db.il",
                     "AI_pdk_capture.il")
    ] + [
        f'ddCreateLib("SicoTest" {json.dumps(str(tmp_path / "SicoTest"))})',
        'aiWriteTextFile("SicoTest/libInit.il" "t\\n")',
        'tf=techOpenTechFile("SicoTest" "tech.db" "w") techSaveTechFile(tf)',
        'foreach(cell list("PROBEGOOD" "PROBEBAD") '
        'v=dbOpenCellViewByType("SicoTest" cell "symbol" "schematicSymbol" "a") '
        'n=dbCreateNet(v "P") term=dbCreateTerm(n "P" "inputOutput") '
        'fig=dbCreateRect(v list("pin" "drawing") list(0:0 0.125:0.125)) '
        'dbCreatePin(n fig nil term) dbSave(v) dbClose(v) '
        'c=cdfCreateBaseCellCDF(ddGetObj("SicoTest" cell)) '
        'unless(cdfCreateParam(c ?name "w" ?type "string" ?prompt "Width" ?defValue "1u" '
        '?units "lengthMetric" ?parseAsNumber "yes") error("Test CDF parameter creation failed")) '
        'unless(cdfCreateParam(c ?name "model" ?type "string" ?prompt "Model" '
        '?defValue if(equal(cell "PROBEGOOD") "PROBEGOOD" "PROBEBAD") ?parseAsNumber "no") '
        'error("Test model parameter creation failed")) cdfSaveCDF(c))',
        'aiPdkCapture("collect" "session:collection" "libraries" nil nil nil)',
        'aiWriteTextFile("libraries.json" aiPdkTransfers["collect"]) aiPdkDrop("collect")',
        'aiPdkCapture("collect" "session:collection" "categories" "SicoTest" nil nil)',
        'aiWriteTextFile("categories.json" aiPdkTransfers["collect"]) aiPdkDrop("collect")',
        'aiPdkCapture("collect" "session:collection" "directory" "SicoTest" nil "symbol")',
        'aiWriteTextFile("directory.json" aiPdkTransfers["collect"]) aiPdkDrop("collect")',
        'foreach(cell list("PROBEGOOD" "PROBEBAD") '
        'aiPdkCapture("collect" "session:collection" "device" "SicoTest" cell "symbol") '
        'aiWriteTextFile(strcat(cell ".json") aiPdkTransfers["collect"]) aiPdkDrop("collect"))',
    ], virtuoso=True)
    captures = {key: json.loads((tmp_path / (key + ".json")).read_text())
                for key in ["libraries", "categories", "directory", *cells]}
    # Source B: the install tree that surrounds the live library (decks + `.Cat` registry).
    (tmp_path / "models" / "spectre").mkdir(parents=True)
    (tmp_path / "models" / "spectre" / "probe.lib").write_text(
        "inline subckt PROBEGOOD (d g s b)\nparameters wmin=1u wmax=2u\nends PROBEGOOD\n")
    (tmp_path / "SicoTest" / "probe.Cat").write_text(
        'SicoTest/PROBEGOOD type="cell"\nSicoTest/GHOST type="cell"\n')

    class CapturedBridge:
        def __init__(self):
            self.calls = []

        def capture(self, operation, target):
            self.calls.append((operation, deepcopy(target)))
            return deepcopy(captures[target["cell"] if operation == "device" else operation])

    bridge = CapturedBridge()
    service = PdkPreparation(bridge, workspace=tmp_path, environment={})
    done = finish(service, service.prepare({"library": "SicoTest"}))
    assert done["status"] == "ready"
    assert done["coverage"]["device_count"] == 2
    assert done["coverage"]["categories"] == {"probe": 1}
    assert done["coverage"]["category_missing"] == 1
    assert done["coverage"]["model_status"] == {"found": 1, "not_found_in_shipped_decks": 1}
    assert done["coverage"]["limits_published"] == 1
    quality = done["quality"]
    assert quality["included_devices"] == 2 and quality["excluded_count"] == 1
    assert quality["excluded_reasons"] == {"registry_stale": 1}
    assert {(row["cell"], row["reason"]) for row in quality["excluded_devices"]} == {
        ("GHOST", "registry_stale")}
    manifest = json.loads(Path(done["path"]).read_text())
    assert set(manifest["payload"]["devices"]) == {"PROBEGOOD", "PROBEBAD"}
    assert manifest["payload"]["excluded"] == {}
    device = json.loads((service.data.root / "devices" / (
        manifest["payload"]["devices"]["PROBEGOOD"]["digest"] + ".json")).read_text())["device"]
    assert device["model_resolution"]["status"] == "found"
    assert device["categories"]["all"] == ["probe"]
    assert device["limits"]["model_deck"]["wmin"]["value"] == 1e-6
    assert device["file_layer"]["available"] is True
    # Reuse stays consistent: the exclusion report is part of the published database.
    cached = PdkPreparation(bridge, workspace=tmp_path, environment={})
    ready = cached.prepare({"library": "SicoTest"})
    assert ready["status"] == "ready" and ready["quality"]["excluded_count"] == 1
    assert ready["coverage"]["device_count"] == 2
