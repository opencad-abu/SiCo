"""Real saved OA bytes and guarded public layout collection in a private SicoTest workspace."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from template_qualification_fixture import collect_rows

from cadai.template_capture import parse_capture
from cadai.template_native_content import fingerprint
from cadai.template_standard.replay import replay

AI = Path(__file__).resolve().parents[2]


def test_guarded_native_layout_and_master_byte_identity(tmp_path):
    exe = shutil.which(os.environ.get("CAD_AI_DBACCESS", "dbAccess"))
    if exe is None:
        pytest.skip("dbAccess unavailable")
    work = tmp_path / "native-work"
    work.mkdir()
    # Test workspace owns SicoTest. No user/PDK views and no editor calls.
    modules = [
        "AI_protocol.il",
        "AI_templates.il",
        "AI_template_capture_geometry.il",
        "AI_template_native_content.il",
    ]
    source = "\n".join("load(" + json.dumps(str(AI / "skill" / n)) + ")" for n in modules)
    source += """
let((master source net term pin)
  when(ddGetObj("SicoTest") error("Unexpected existing test library binding"))
  ddCreateLib("SicoTest" strcat(getWorkingDir() "/SicoTest space'quote"))
  master=dbOpenCellViewByType("SicoTest" "static_master" "layout" "maskLayout" "w")
  dbCreateRect(master list("device" "drawing") list(0:0 1:1))
  net=dbCreateNet(master "P")
  term=dbCreateTerm(net "P" "inputOutput")
  pin=dbCreateRect(master list("pin" "drawing") list(0:0 0.1:0.1))
  dbCreatePin(net pin "P" term)
  dbSave(master)
  source=dbOpenCellViewByType("SicoTest" "layout_source" "layout" "maskLayout" "w")
  dbCreateInst(source master "P0" 0:0 "R0")
  dbSave(source)
  dbClose(source)
  dbClose(master)
)
aiTplCapture("SicoTest" "layout_source" "schematic" "symbol" "layout"
 "first.jsonl" list("layout") "cad.template.capture.v2")
let((master)
  master=dbOpenCellViewByType("SicoTest" "static_master" "layout" "maskLayout" "a")
  dbCreateRect(master list("device" "drawing") list(2:2 3:3))
  dbSave(master)
  dbClose(master)
)
aiTplCapture("SicoTest" "layout_source" "schematic" "symbol" "layout"
 "second.jsonl" list("layout") "cad.template.capture.v2")
; Inject an inconsistent second observation without touching source OA.
putd('nativeOriginal getd('aiTplNativeContent))
nativeCalls=0
procedure(aiTplNativeContent(probeCv)
  nativeCalls=nativeCalls+1
  if(nativeCalls==2 then "{}" else nativeOriginal(probeCv)))
unless(null(errset(aiTplCapture("SicoTest" "layout_source" "schematic" "symbol" "layout"
 "changed.jsonl" list("layout") "cad.template.capture.v2") nil))
 error("Changed source evidence was accepted"))
when(isFile("changed.jsonl") error("Failed capture left a complete artifact"))
printf("NATIVE_CONTENT_PASS\\n")
exit()
"""
    (work / "run.il").write_text(source)
    with (work / "native.log").open("w") as log:
        proc = subprocess.run(
            [exe],
            input=source,
            cwd=work,
            text=True,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=120,
        )
    output = (work / "native.log").read_text()
    assert proc.returncode == 0 and "*Error*" not in output, output
    first = parse_capture(work / "first.jsonl")
    second = parse_capture(work / "second.jsonl")
    views1 = first["assets"]["layout"]["header"]["native_content"]["views"]
    views2 = second["assets"]["layout"]["header"]["native_content"]["views"]
    old = next(v for v in views1 if v["role"] == "master")
    new = next(v for v in views2 if v["role"] == "master")
    assert fingerprint(old) != fingerprint(new)
    assert new["terminals"] == [dict(name="P", direction="inputOutput", width=1)]
    for view in views2:
        for member in view["files"]:
            path = (
                Path(view["library_path"]) / view["target"][1] / view["target"][2] / member["name"]
            )
            assert hashlib.sha256(path.read_bytes()).hexdigest() == member["sha256"]
    rows = [json.loads(line) for line in (work / "second.jsonl").read_text().splitlines()]
    result, item = collect_rows(tmp_path, rows, dict(library="SicoTest", cell="layout_source"))
    assert result["capabilities"]["layout-reference-v1"] == "passed"
    assert item["documents"]["layout"]["binding_status"] == "unavailable"
    # Saved package/receipt can be read and replayed without dereferencing the native library.
    replay(item)
