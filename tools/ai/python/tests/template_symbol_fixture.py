"""Owned Virtuoso fixture and schematic sources for symbol acceptance."""

import json
import os
import select
import shutil
import signal
import subprocess
import uuid

import pytest
from test_circuit_rc_gui import AI_ROOT, DispatchClient, NativeBridge
from test_template_symbol import stress_record

from cadai.controller import RequestDispatcher
from cadai.mcp import McpServer
from cadai.runtime import RuntimePaths
from cadai.template_build import make_template, publish
from cadai.template_capture import read_capture
from cadai.template_catalog import TemplateCatalog

REFERENCE = AI_ROOT.parent / ".cad/ai/reference/circuit-templates"
REFERENCE_CDS = AI_ROOT.parent / ".cad/circuit-workflow-study/template-capture/cds.lib"


@pytest.fixture
def symbol_session(tmp_path, monkeypatch):
    if os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1":
        pytest.skip("set CAD_AI_RUN_GUI_TESTS=1 for licensed symbol acceptance")
    virtuoso, xvfb = shutil.which("virtuoso"), shutil.which("Xvfb")
    if not virtuoso or not xvfb or not REFERENCE_CDS.is_file():
        pytest.skip("local Virtuoso/Xvfb/reference libraries unavailable")
    (tmp_path / "cds.lib").write_text("INCLUDE " + str(REFERENCE_CDS) + "\n")
    catalog = TemplateCatalog(REFERENCE)
    summary = catalog.query(
        {
            "library": "gsclib045",
            "cell": "INVX1",
            "required_asset": "symbol",
            "rule_version": "20260910.4",
            "offset": 0,
            "limit": 1,
        }
    )
    inverter = catalog.get(summary["templates"][0]["template_ref"])
    stress = stress_record()
    root = tmp_path / "catalog"
    root.mkdir()
    monkeypatch.setenv("SICO_CIRCUIT_TEMPLATES_DIR", str(root))
    lib = "templateSymbol" + uuid.uuid4().hex[:8]
    modules = [
        "AI_protocol.il",
        "AI_eval.il",
        "AI_circuit.il",
        "AI_circuit_task.il",
        "AI_templates.il",
        "AI_circuit_symbol.il",
        "AI_template_symbol_geometry.il",
        "AI_template_symbol.il",
    ]
    lines = ["load(" + json.dumps(str(AI_ROOT / "skill" / n)) + ")" for n in modules]
    lines += [
        f'ddCreateLib("{lib}" {json.dumps(str(tmp_path / "worklib"))})',
        'procedure(symbolTestPoll() when(isFile("command.il") '
        'load("command.il") deleteFile("command.il")) '
        'hiRegTimer("symbolTestPoll()" 1))',
        "symbolTestPoll()",
        'aiWriteTextFile("ready" "yes")',
    ]
    (tmp_path / "run.il").write_text("\n".join(lines) + "\n")
    rfd, wfd = os.pipe()
    xproc = subprocess.Popen(
        [xvfb, "-displayfd", str(wfd), "-screen", "0", "1440x1000x24", "-nolisten", "tcp"],
        pass_fds=(wfd,),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    os.close(wfd)
    proc = None
    try:
        assert select.select([rfd], [], [], 10)[0]
        display = os.read(rfd, 32).decode().strip()
        assert display.isdigit()
        with (tmp_path / "process.log").open("w") as log:
            proc = subprocess.Popen(
                [virtuoso, "-nocdsinit", "-norestore", "-replay", "run.il", "-log", "CDS.log"],
                cwd=tmp_path,
                env=dict(os.environ, DISPLAY=":" + display),
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        bridge = NativeBridge(tmp_path, proc)
        bridge.wait_file(tmp_path / "ready")
        source_lib = lib + "Source"
        bridge.skill(f'ddCreateLib("{source_lib}" {json.dumps(str(tmp_path / "source"))})')
        scratch_schematic(bridge, source_lib, "Stress", stress)
        capture = tmp_path / "stress.jsonl"
        bridge.skill(
            f'aiTplCapture("{source_lib}" "Stress" "schematic" "symbol" "layout" '
            f'{json.dumps(str(capture))} list("schematic"))'
        )
        stress = make_template(read_capture(capture))
        assert stress["source"]["net_global_semantics"] == "uniform_boolean_v1"
        bus = next(n for n in stress["topology"]["nets"] if n["source_name"] == "DATA<7:0>")
        assert bus["is_global"] is False
        publish([inverter, stress], root / "catalog.sqlite3")
        spool = tmp_path / "spool"
        spool.mkdir(mode=0o700)
        server = McpServer(
            DispatchClient(
                RequestDispatcher(
                    bridge, RuntimePaths(tmp_path, tmp_path / "sock", spool), tmp_path
                )
            ),
            workspace=tmp_path,
        )
        yield server, bridge, lib, inverter, stress
    finally:
        if proc and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=10)
        xproc.terminate()
        xproc.wait(timeout=10)
        os.close(rfd)


def scratch_schematic(bridge, lib, cell, record, *, actual=False):
    if actual:
        bridge.skill(
            f'let((s c) s=dbOpenCellViewByType("gsclib045" "INVX1" "schematic" "" "r") '
            f'c=dbCopyCellView(s "{lib}" "{cell}" "schematic") '
            'unless(c error("copy failed")) dbSave(c) dbClose(c) dbClose(s) t)'
        )
    else:
        nets = {n["id"]: n for n in record["topology"]["nets"]}
        parts = [
            f"let((tscCv tscPin tscMaster tscW tscNet) "
            f'tscCv=dbOpenCellViewByType("{lib}" "{cell}" "schematic" "schematic" "a")'
        ]
        for i, p in enumerate(record["topology"]["ports"]):
            n = nets[p["net"]]
            master = {"input": "ipin", "output": "opin", "inputOutput": "iopin"}[p["direction"]]
            name, direction, net = (
                json.dumps(v) for v in (p["name"], p["direction"], n["source_name"])
            )
            parts += [
                f'tscMaster=dbOpenCellViewByType("basic" "{master}" "symbol" "" "r")',
                f'tscPin=schCreatePin(tscCv tscMaster {name} {direction} nil list(0 {-i}) "R0")',
                'unless(tscPin error("fixture pin failed")) dbClose(tscMaster)',
                f'tscW=schCreateWire(tscCv "draw" "full" list(list(0 {-i}) list(0.5 {-i})) '
                '0.0625 0.0625 0.0)',
                f'schCreateWireLabel(tscCv car(tscW) list(0.25 {-i + 0.125}) {net} '
                '"lowerLeft" "R0" "stick" 0.125 nil)',
            ]
        parts += ["schCheck(tscCv)"]
        for p in record["topology"]["ports"]:
            n = nets[p["net"]]
            parts += [
                f"tscNet=dbFindTermByName(tscCv {json.dumps(p['name'])})~>net",
                f"tscNet~>sigType={json.dumps(n['sig_type'])} "
                f"tscNet~>isGlobal={'t' if n['is_global'] else 'nil'}",
            ]
        bridge.skill(" ".join(parts) + " dbSave(tscCv) dbClose(tscCv) t)")
