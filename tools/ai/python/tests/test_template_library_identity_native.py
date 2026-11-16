"""Owned Virtuoso evidence for name-neutral capture and symbol target protection."""

import hashlib
import json
import os

import pytest
from circuit_create_support import AI_ROOT, session
from template_symbol_fixture import scratch_schematic
from test_circuit_rc_gui import IC_LIBS, mcp_call
from test_template_symbol import example_record

from cadai.template_catalog import TemplateCatalog
from cadai.template_classification import RULE_VERSION, SEMANTICS

pytestmark = pytest.mark.skipif(os.environ.get("SICO_AI_RUN_GUI_TESTS") != "1",
                                reason="licensed library identity acceptance")


def _primitive(bridge, library):
    bridge.skill(
        f'let((v n term fig) v=dbOpenCellViewByType("{library}" "nmos1v" '
        '"symbol" "schematicSymbol" "a") '
        'dbCreateRect(v list("device" "drawing") list(0:0 1:1)) '
        'foreach(p list(list("PLUS" 0:0) list("MINUS" 1:0)) '
        'n=dbCreateNet(v car(p)) term=dbCreateTerm(n car(p) "inputOutput") '
        'fig=dbCreateRect(v list("pin" "drawing") '
        'list(cadr(p) list(xCoord(cadr(p))+0.0625 yCoord(cadr(p))+0.0625))) '
        'dbCreatePin(n fig)) dbSave(v) dbClose(v) t)'
    )


def _instance(bridge, library, cell, master_library):
    bridge.skill(
        f'let((v m) v=dbOpenCellViewByType("{library}" "{cell}" "schematic" "" "a") '
        f'm=dbOpenCellViewByType("{master_library}" "nmos1v" "symbol" "" "r") '
        'dbCreateInst(v m "I_device_0" 3:0 "R0") dbSave(v) dbClose(m) dbClose(v) t)'
    )


def _alias(path, bridge, name, target, original):
    # DD keeps one visible name per physical library; rename the private definition.
    cds = path / "cds.lib"
    lines = [line for line in cds.read_text().splitlines()
             if not line.startswith(f"DEFINE {original} ")]
    cds.write_text("\n".join(lines) + f"\nDEFINE {name} {target}\n")
    assert bridge.skill("progn(ddSetForcedLib(" + json.dumps(str(path / "cds.lib"))
                        + ") ddUpdateLibList())") == "t"
    assert bridge.skill(f'ddGetObj({json.dumps(name)})') != "nil"


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_capture_classification_and_target_library_identity(tmp_path, monkeypatch):
    with session(tmp_path) as (bridge, server, _):
        for name in ("AI_templates.il", "AI_template_capture_geometry.il", "AI_circuit_symbol.il",
                     "AI_template_symbol_geometry.il", "AI_template_symbol.il"):
            bridge.skill("load(" + json.dumps(str(AI_ROOT / "skill" / name)) + ")")
        for name in ("sourceOwned", "gpdk045", "deviceOwned", "techOwned"):
            bridge.skill(f'ddCreateLib("{name}" {json.dumps(str(tmp_path / name))})')
        # Rename builtins before opening source views; DD refuses open-library renames.
        _alias(tmp_path, bridge, "baseAlias", IC_LIBS / "basic", "basic")
        assert bridge.skill('aiTplBuiltinLibrary("baseAlias")') == "basic"
        _alias(tmp_path, bridge, "basic", IC_LIBS / "basic", "baseAlias")
        _alias(tmp_path, bridge, "analogAlias", IC_LIBS / "artist/analogLib", "analogLib")
        _alias(tmp_path, bridge, "deviceAlias", tmp_path / "deviceOwned", "deviceOwned")
        _primitive(bridge, "deviceAlias")
        base = example_record()
        scratch_schematic(bridge, "sourceOwned", "source", base)
        _instance(bridge, "sourceOwned", "source", "deviceAlias")
        source_oa = tmp_path / "sourceOwned/source/schematic/sch.oa"
        master_oa = tmp_path / "deviceOwned/nmos1v/symbol/symbol.oa"
        before = [_hash(p) for p in (source_oa, master_oa)]
        original_request = bridge.request

        def request(message, timeout):
            if message["method"] == "capture_circuit_template":
                bridge.evaluate_path(message["source_path"], message["output_path"],
                                     evaluator="aiEvalExpression")
                return True, {"result_path": message["output_path"]}
            return original_request(message, timeout)

        monkeypatch.setattr(bridge, "request", request)
        server.client.runtime = server.client.dispatcher.runtime
        capture_args = dict(library="sourceOwned", cell="source", assets=["schematic"],
                            capture_schema="cad.template.capture.v2")
        captured = mcp_call(server, "extract_circuit_templates", capture_args)
        assert captured["ok"], captured
        catalog = TemplateCatalog(workspace=tmp_path)
        record = catalog.get(captured["template_ref"])
        assert record["source"]["classification_semantics"] == SEMANTICS
        assert record["rule_version"] == RULE_VERSION
        assert record["topology"]["devices"][0]["kind"] == "unknown"
        typed = mcp_call(server, "extract_circuit_templates", capture_args | {
            "classifications": {"I_device_0": dict(kind="resistor", attributes={},
                            source_ref="test:observed-primitive", revision="v1")}
        })
        assert typed["ok"], typed
        assert typed["template_ref"] != record["template_ref"]
        classified = catalog.get(typed["template_ref"])
        assert classified["topology"]["devices"][0]["kind"] == "resistor"
        assert catalog.get(record["template_ref"]) == record

        # Builtin aliases are observed by installed path; familiar names alone are not proof.
        assert bridge.skill('aiTplBuiltinLibrary("gpdk045")') == "nil"
        assert bridge.skill('aiTplBuiltinLibrary("deviceAlias")') == "nil"
        source_alias = tmp_path / "source-link"
        source_alias.symlink_to(tmp_path / "sourceOwned", target_is_directory=True)
        _alias(tmp_path, bridge, "sourceAlias", source_alias, "sourceOwned")

        task = mcp_call(server, "begin_circuit_task", dict(task_id="identity",
                        presentation="background", choice_confirmed=True))

        def preview(library, cell, request_id):
            return mcp_call(server, "preview_template_symbol", dict(
                library=library, cell=cell, template_ref=record["template_ref"],
                task_ref=task["task_ref"], request_id=request_id))

        # A work library named after the former hard-coded PDK is valid.
        bridge.skill('let((tf) tf=techOpenTechFile("techOwned" "tech.db" "a") '
                     'techSaveTechFile(tf) techCloseTechFile(tf) t)')
        assert bridge.skill('techBindTechFile(ddGetObj("gpdk045") "techOwned")') == "t"
        scratch_schematic(bridge, "gpdk045", "accepted", base)
        _instance(bridge, "gpdk045", "accepted", "deviceAlias")
        accepted = preview("gpdk045", "accepted", "accept")
        assert accepted["ok"], accepted
        created = mcp_call(server, "create_template_symbol", dict(
            preview_ref=accepted["preview_ref"], preview_digest=accepted["preview_digest"],
            request_id="create"))
        assert created["ok"] and created["saved"] and created["readback_verified"], created
        assert created["interface_check"] == [0, 0]
        assert mcp_call(server, "inspect_template_symbol",
                        dict(symbol_ref=created["symbol_ref"]))["ok"]

        # Actual referenced leaf library (including its alias) remains protected.
        scratch_schematic(bridge, "deviceAlias", "refused", base)
        _instance(bridge, "deviceAlias", "refused", "deviceAlias")
        # Technology ownership is observed; the test creates only a private technology DB.
        scratch_schematic(bridge, "techOwned", "refused", base)
        refusals = [preview(lib, cell, lib) for lib, cell in (
            ("sourceAlias", "source"), ("deviceAlias", "refused"),
            ("techOwned", "refused"), ("analogAlias", "missing"))]
        assert all(not row["ok"] and row["code"] == "symbol_preview_failed" for row in refusals)
        assert "separate work library" in refusals[0]["message"]
        assert "device library" in refusals[1]["message"]
        assert "technology library" in refusals[2]["message"]
        assert "Cadence installation" in refusals[3]["message"]
        for lib in ("deviceAlias", "techOwned"):
            assert bridge.skill(f'ddGetObj("{lib}" "refused" "symbol")') == "nil"

        # Dependency remapping after preview invalidates creation before OA writes.
        scratch_schematic(bridge, "gpdk045", "stale", base)
        _instance(bridge, "gpdk045", "stale", "deviceAlias")
        stale = preview("gpdk045", "stale", "stale")
        assert stale["ok"], stale
        bridge.skill(f'ddCreateLib("replacement" {json.dumps(str(tmp_path / "replacement"))})')
        _primitive(bridge, "replacement")
        # Close cached views in this owned session so DD can actually remap the library.
        bridge.skill('progn(foreach(cv dbGetOpenCellViews() '
                     'when(dbIsCellViewModified(cv) error("fixture has unsaved view"))) '
                     'while(dbGetOpenCellViews() dbPurge(car(dbGetOpenCellViews()))) t)')
        cds = tmp_path / "cds.lib"
        lines = [line for line in cds.read_text().splitlines()
                 if not line.startswith("DEFINE replacement ")]
        cds.write_text("\n".join(lines).replace(
            "DEFINE deviceAlias " + str(tmp_path / "deviceOwned"),
            "DEFINE deviceAlias " + str(tmp_path / "replacement")) + "\n")
        assert bridge.skill("ddUpdateLibList()") == "t"
        assert bridge.skill('ddGetObjReadPath(ddGetObj("deviceAlias"))') == str(
            tmp_path / "replacement")
        rejected = mcp_call(server, "create_template_symbol", dict(
            preview_ref=stale["preview_ref"], preview_digest=stale["preview_digest"],
            request_id="stale-create"))
        assert not rejected["ok"] and not rejected["view_created"], rejected
        assert rejected["stage"] == "preflight"
        assert bridge.skill('ddGetObj("gpdk045" "stale" "symbol")') == "nil"
        changed = mcp_call(server, "inspect_template_symbol",
                           dict(symbol_ref=created["symbol_ref"]))
        assert not changed["ok"] and changed["code"] == "symbol_changed", changed
        assert before == [_hash(p) for p in (source_oa, master_oa)]
        (tmp_path / "acceptance.json").write_text(json.dumps(dict(
            capture=captured, classified=typed, created=created, refusals=refusals,
            remapped=rejected, source_and_master_unchanged=True), indent=2))


@pytest.mark.parametrize("mode", ["foreground", "background"])
def test_same_library_design_block_allowed_but_recursive_target_refused(tmp_path, mode):
    from cadai.template_build import publish

    with session(tmp_path) as (bridge, server, _):
        for name in ("AI_templates.il", "AI_circuit_symbol.il",
                     "AI_template_symbol_geometry.il", "AI_template_symbol.il"):
            bridge.skill("load(" + json.dumps(str(AI_ROOT / "skill" / name)) + ")")
        record = example_record()
        publish([record], TemplateCatalog(workspace=tmp_path).root / "catalogs/fixture.sqlite3")
        task = mcp_call(server, "begin_circuit_task", dict(task_id="design",
                        presentation=mode, choice_confirmed=True))
        _primitive(bridge, "genericWork")
        scratch_schematic(bridge, "genericWork", "nmos1v", record)
        scratch_schematic(bridge, "genericWork", "parent", record)
        _instance(bridge, "genericWork", "parent", "genericWork")
        preview = mcp_call(server, "preview_template_symbol", dict(
            library="genericWork", cell="parent", template_ref=record["template_ref"],
            task_ref=task["task_ref"], request_id="parent"))
        assert preview["ok"], preview
        result = mcp_call(server, "create_template_symbol", dict(
            preview_ref=preview["preview_ref"], preview_digest=preview["preview_digest"],
            request_id="parent"))
        assert result["ok"] and result["saved"] and result["readback_verified"], result
        assert result["interface_check"] == [0, 0] and result["presentation"] == mode
        assert mcp_call(server, "inspect_template_symbol",
                        dict(symbol_ref=result["symbol_ref"]))["ok"]
        _instance(bridge, "genericWork", "nmos1v", "genericWork")
        refused = mcp_call(server, "preview_template_symbol", dict(
            library="genericWork", cell="nmos1v", template_ref=record["template_ref"],
            task_ref=task["task_ref"], request_id="recursive"))
        assert not refused["ok"] and "source design cell" in refused["message"], refused
        (tmp_path / "acceptance.json").write_text(json.dumps(dict(
            mode=mode, created=result, recursive=refused), indent=2))
