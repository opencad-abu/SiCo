"""Owned native proof for aliased builtin graphics, hierarchy, and geometry failure feedback."""

import hashlib
import json
import os
from pathlib import Path

import pytest
from circuit_create_support import AI_ROOT, session
from test_circuit_rc_gui import IC_LIBS, mcp_call
from test_template_library_identity_native import _alias, _primitive

from cadai.template_catalog import TemplateCatalog
from cadai.template_source_qualification import require_qualified

pytestmark = pytest.mark.skipif(os.environ.get("SICO_AI_RUN_GUI_TESTS") != "1",
                                reason="licensed capture boundary acceptance")


def test_builtin_graphics_hierarchy_and_geometry_failure(tmp_path, monkeypatch):
    with session(tmp_path) as (bridge, server, _):
        for name in ("AI_templates.il", "AI_template_capture_geometry.il"):
            bridge.skill("load(" + json.dumps(str(AI_ROOT / "skill" / name)) + ")")
        # Chunk boundaries preserve the protocol's escaping, including Unicode.
        sample = 'a' * 255 + '\"\\\n\t中文' + 'b' * 260
        encoded = json.dumps(sample, ensure_ascii=False)
        code = ('let((s) s=' + encoded
                + ' equal(aiTplJson(s) strcat("\\\"" aiJsonEscape(s) "\\\"")))')
        assert bridge.skill(code) == "t"
        _alias(tmp_path, bridge, "baseAlias", IC_LIBS / "basic", "basic")
        _alias(tmp_path, bridge, "supplyAlias", IC_LIBS / "artist/analogLib", "analogLib")
        _primitive(bridge, "genericWork")
        bridge.skill('let((v m inst net) v=dbOpenCellViewByType("genericWork" "direct" '
                     '"schematic" "schematic" "a") '
                     'm=dbOpenCellViewByType("genericWork" "nmos1v" "symbol" "" "r") '
                     'inst=dbCreateInst(v m "D0" 3:0 "R0") '
                     'net=dbCreateNet(v "A") '
                     'foreach(term m~>terminals dbCreateInstTerm(net inst term)) dbClose(m) '
                     'm=dbOpenCellViewByType("supplyAlias" "gnd" "symbol" "" "r") '
                     'dbCreateInst(v m "G0" 0:0 "R0") dbClose(m) '
                     'm=dbOpenCellViewByType("baseAlias" "ipin" "symbol" "" "r") '
                     'schCreatePin(v m "A" "input" nil 0:2 "R0") dbClose(m) '
                     'dbSave(v) dbClose(v) t)')
        source = tmp_path / "library/direct/schematic/sch.oa"
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        original_request = bridge.request

        def request(message, timeout):
            if message["method"] == "capture_circuit_template":
                bridge.evaluate_path(message["source_path"], message["output_path"],
                                     evaluator="aiEvalExpression")
                return True, {"result_path": message["output_path"]}
            return original_request(message, timeout)

        monkeypatch.setattr(bridge, "request", request)
        server.client.runtime = server.client.dispatcher.runtime
        args = dict(library="genericWork", cell="direct", assets=["schematic"],
                    reuse=dict(mode="whole", allow_rename=False))
        evidence = dict(kind="resistor", attributes={},
                        source_ref="test:owned-symbol", revision="1")
        partial = mcp_call(server, "extract_circuit_templates", args)
        assert partial["reuse"]["status"] == "needs_classification", partial
        # A pinned partial capture is enough; forbid any subsequent native call for completion.
        monkeypatch.setattr(server.client, "call",
                            lambda *a, **kw: pytest.fail("unexpected native call"))
        completed = mcp_call(server, "extract_circuit_templates",
                             partial["continuation"]["arguments"]
                             | dict(classifications={"D0": evidence}))
        assert completed["ok"] and completed["live"] is False, completed
        monkeypatch.undo()
        monkeypatch.setattr(bridge, "request", request)
        record = TemplateCatalog(workspace=tmp_path).get(completed["template_ref"])
        assert require_qualified(record)
        assert len(record["topology"]["devices"]) == 1
        assert {g["role"] for g in record["topology"]["ignored_graphics"]} == {
            "supply_marker", "port_graphic"}
        ports = record["assets"]["schematic"]["wire_reference"]["ports"]
        assert len(ports) == 1 and ports[0]["port"] == "A"
        assert hashlib.sha256(source.read_bytes()).hexdigest() == before

        # A saved disconnected endpoint remains a reference, even with complete classifications.
        bridge.skill('let((v inst) v=dbOpenCellViewByType("genericWork" "direct" '
                     '"schematic" "" "a") inst=car(setof(i v~>instances i~>name=="D0")) '
                     'dbDeleteObject(car(setof(term inst~>instTerms term~>name=="MINUS"))) '
                     'dbSave(v) dbClose(v) t)')
        open_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        unconnected = mcp_call(server, "extract_circuit_templates",
                               args | dict(classifications={"D0": evidence}))
        assert not unconnected["ok"] and unconnected["stage"] == "stored_reference", unconnected
        endpoint_issue = next(i for i in unconnected["reuse"]["issues"]
                              if i["code"] == "unconnected_endpoint")
        assert endpoint_issue["object_ref"] == "D0" and endpoint_issue["terminal"] == "MINUS"
        assert hashlib.sha256(source.read_bytes()).hexdigest() == open_hash
        # Restore only the owned fixture so the next case isolates hierarchy.
        bridge.skill('let((v inst term) v=dbOpenCellViewByType("genericWork" "direct" '
                     '"schematic" "" "a") inst=car(setof(i v~>instances i~>name=="D0")) '
                     'term=car(setof(p inst~>master~>terminals p~>name=="MINUS")) '
                     'dbCreateInstTerm(dbFindNetByName(v "A") inst term) '
                     'dbSave(v) dbClose(v) t)')

        # Adding a real schematic to the owned electrical master must still reject hierarchy.
        bridge.skill('let((v) v=dbOpenCellViewByType("genericWork" "nmos1v" '
                     '"schematic" "schematic" "a") dbSave(v) dbClose(v) t)')
        hierarchy = mcp_call(server, "extract_circuit_templates", args)
        assert not hierarchy["ok"] and hierarchy["reuse"]["template_ref"] is None, hierarchy
        assert hierarchy["reuse"]["issues"][0]["code"] == "unsupported_source_role"

        # A large but bounded flat master exercises the real signature budget failure.
        bridge.skill('let((v m) v=dbOpenCellViewByType("genericWork" "large" '
                     '"symbol" "schematicSymbol" "a") '
                     'for(i 0 1499 dbCreateRect(v list("device" "drawing") '
                     'list(list(i/100.0 0) list(i/100.0+0.0625 1)))) '
                     'dbSave(v) dbClose(v) '
                     'v=dbOpenCellViewByType("genericWork" "failure" "schematic" "schematic" "a") '
                     'm=dbOpenCellViewByType("genericWork" "large" "symbol" "" "r") '
                     'dbCreateInst(v m "BIG" 0:0 "R0") dbSave(v) dbClose(m) dbClose(v) t)')
        failed = mcp_call(server, "extract_circuit_templates", args | dict(cell="failure"))
        assert not failed["ok"] and failed["stage"] == "stored_reference", failed
        issue = failed["diagnostics"]["issues"][0]
        assert issue["object_ref"] == "BIG" and issue["cause_code"] == "pdk_body_unavailable"
        assert "body signature exceeds adapter budget" in issue["message"]
        assert failed["reuse"]["template_ref"] is None
        Path(tmp_path / "acceptance.json").write_text(json.dumps(dict(
            completed=completed, unconnected=unconnected, hierarchy=hierarchy,
            geometry_failure=failed,
            source_unchanged=True), indent=2))
