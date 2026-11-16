"""Opt-in licensed read-only acceptance; no test cells or CDFs are created."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from cadai.pdk_normalize import context, detail
from cadai.skill_check import check_source

AI = Path(__file__).resolve().parents[2]
SOURCES = [
    "AI_protocol.il", "AI_pdk_core.il", "AI_pdk_init.il", "AI_pdk_cdf.il", "AI_pdk_db.il",
    "AI_pdk_capture.il",
]


def test_pdk_skill_preflight_and_no_mutating_or_evaluation_apis():
    for name in SOURCES[1:]:
        source = (AI / "skill" / name).read_bytes()
        assert check_source(source, source_name=name)["ok"]
        text = source.decode()
        for forbidden in ("dbSave(", "dbCreate", "cdfCreate", "cdfSave", "cdfgData=", "evalstring(",
                          "errsetstring(", "hiCloseWindow(", "dbPurge(", "cdfGetInstCDF("):
            assert forbidden not in text


@pytest.mark.skipif(os.environ.get("SICO_AI_RUN_PDK_NATIVE") != "1", reason="opt-in licensed PDK acceptance")
def test_installed_device_metadata_and_read_lifecycle(tmp_path):
    binary = shutil.which("dbAccess")
    if not binary:
        pytest.skip("dbAccess unavailable")
    lib = os.environ.get("SICO_AI_PDK_TEST_LIBRARY", "analogLib")
    cell = os.environ.get("SICO_AI_PDK_TEST_CELL", "res")
    source = os.environ.get("SICO_AI_PDK_TEST_CDS_LIB")
    if source:
        (tmp_path / "cds.lib").write_text("INCLUDE " + source + "\n")
    q = json.dumps
    script = "\n".join(f"load({q(str(AI / 'skill' / name))})" for name in SOURCES)
    script += f'''
procedure(pdkTestWrite(pdkTestName pdkTestValue)
 let((pdkTestPort) pdkTestPort=outfile(pdkTestName "w")
 fprintf(pdkTestPort "%s" pdkTestValue) close(pdkTestPort)))
pdkBefore=dbGetOpenCellViews()
pdkTestWrite("capture-status.json" aiPdkCapture("test" "session:native" "device" {q(lib)} {q(cell)} "symbol"))
pdkTestWrite("device.json" aiPdkTransfers["test"])
unless(equal(pdkBefore dbGetOpenCellViews()) error("owned read reference leaked"))
aiPdkDrop("test")
pdkTestWrite("empty-cdf-status.json" aiPdkCapture("test" "session:native" "device" "basic" "ipin" "symbol"))
pdkTestWrite("empty-cdf.json" aiPdkTransfers["test"])
aiPdkDrop("test")
pdkBorrowed=dbOpenCellViewByType({q(lib)} {q(cell)} "symbol" "schematicSymbol" "r")
pdkTestWrite("borrowed-status.json" aiPdkCapture("test" "session:native" "device" {q(lib)} {q(cell)} "symbol"))
unless(member(pdkBorrowed dbGetOpenCellViews()) error("borrowed reference closed"))
aiPdkDrop("test")
dbClose(pdkBorrowed)
procedure(aiPdkPorts(apPortsCv) error("intentional read failure"))
pdkTestWrite("failure-status.json" aiPdkCapture("test" "session:native" "device" {q(lib)} {q(cell)} "symbol"))
pdkTestWrite("failure.json" aiPdkTransfers["test"])
unless(equal(pdkBefore dbGetOpenCellViews()) error("failure cleanup leaked"))
aiPdkDrop("test")
unless(aiPdkTransferCount==0 error("serialized transfer leaked"))
printf("PDK_NATIVE_READ_ONLY_OK\\n")
exit()
'''
    (tmp_path / "probe.il").write_text(script)
    result = subprocess.run([binary, "-nocdsinit"], input=script, text=True, capture_output=True,
                            cwd=tmp_path, timeout=45)
    output = result.stdout + result.stderr
    (tmp_path / "probe.log").write_text(output)
    assert result.returncode == 0 and "PDK_NATIVE_READ_ONLY_OK" in output, output
    assert "*Error*" not in output and "PARSER WARNING" not in output, output
    raw = json.loads((tmp_path / "device.json").read_text())
    value = detail(raw["data"], context(raw["context"]))
    assert value["target"] == {"library": lib, "cell": cell, "view": "symbol"}
    assert value["parameters"]["count"] > 0 and value["ports"]["count"] > 0
    assert value["geometry"]["count"] > 0 and value["simulators"]["count"] > 0
    assert value["master_state"]["modified"] is False
    assert all(value[k]["status"] == "complete" for k in ("ports", "parameters", "geometry", "simulators"))
    assert "context_required" in {p["display"]["status"] for p in value["parameters"]["items"]}
    assert "db:0x" not in json.dumps(value) and "cdf:0x" not in json.dumps(value)
    failed = json.loads((tmp_path / "failure.json").read_text())
    assert failed["data"]["database"]["ports"]["status"] == "unavailable"
    empty = json.loads((tmp_path / "empty-cdf.json").read_text())
    # basic/ipin inherits an effective CDF object with zero parameters.
    assert empty["data"]["cdf"]["presence"] == "present"
    assert empty["data"]["cdf"]["parameters"]["items"] == []
    assert empty["data"]["cdf"]["parameters"]["status"] == "complete"
