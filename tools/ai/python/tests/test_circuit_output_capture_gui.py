"""Owned Virtuoso: synchronous workflows over SiCo, then native ADE simulation."""

import json
import os
import shlex
import shutil
import time

import pytest
from circuit_create_fixture import fixture
from circuit_create_support import AI_ROOT, sample_master, session
from simulation_recipe_fixture import recipe
from test_simulation_recipe_gui import load_backend, source_fixture

from sico.adapters.virtuoso import context_tools
from sico.core.contracts import ToolCall
from sico.interpreter import cad_python
from sico.storage.journal import SessionJournal
from sico.transport.broker import ContextBroker
from cadai.template_build import make_template, publish
from cadai.template_capture import read_capture


@pytest.mark.skipif(os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1", reason="licensed GUI acceptance")
def test_creation_capture_and_later_ade_in_same_process(tmp_path, monkeypatch):
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    monkeypatch.setenv("SICO_CIRCUIT_TEMPLATES_DIR", str(catalog))
    with session(tmp_path) as (native, server, process):
        native.skill("ddSetForcedLib(" + json.dumps(str(tmp_path / "cds.lib")) + ")")
        native.skill("load(" + json.dumps(str(AI_ROOT.parent / "sico/skill++/SICO.ils")) + ")")
        native.skill(
            "progn(ocBuildOriginal=getd('aiCrBuild) "
            'procedure(ocBuild(cv entry) printf("BUILD_OUTPUT\\n") '
            # Flush the first warning before native calls can consume the last.
            'warn("BUILD_WARNING\\n") warn("BUFFERED\\n") '
            'fprintf(errport "BUILD_ERROR_PORT\\n") '
            "apply(ocBuildOriginal list(cv entry))) putd('aiCrBuild getd('ocBuild)) "
            "ocOriginalPo=poport ocOriginalWo=woport ocOriginalErr=errport t)"
        )
        masters = {cell: sample_master(native, cell) for cell in ("res", "cap")}
        with SessionJournal(tmp_path, "capture") as journal, ContextBroker(25) as broker:
            configuration = journal.directory / "relay.json"
            configuration.write_text(json.dumps(dict(
                host=broker.address[0], port=broker.address[1], token=broker.token)))
            configuration.chmod(0o600)
            command = shlex.join([cad_python(), "-s", str(AI_ROOT.parent / "sico/python/sico-entry"),
                                  "relay", "--connection-file", str(configuration)])
            native.skill("ocCid=sicoConnect(" + json.dumps(command)
                         + ' "capture-instance" "capture-generation" hiGetCIWindow())')
            evidence = []
            try:
                context = broker.context("capture-instance", timeout=10)
                registry = context_tools(broker, journal)

                def call(name, args):
                    result = registry.execute(ToolCall("capture", name, args), context)
                    evidence.append(dict(tool=name, result=result.record()))
                    assert result.status == "ok", result
                    return result.data

                def preflight(cell, mode, view="schematic", intent="create", library="genericWork"):
                    return call("preflight_circuit_project", dict(
                        presentation=mode,
                        targets=[dict(library=library, cell=cell, view=view,
                                      oa_view_type="schematicSymbol" if view == "symbol" else view,
                                      intent=intent)],
                        libraries=[dict(library=library, expected_path=str(
                            tmp_path / ("library" if library == "genericWork" else library)))],
                        directories=[dict(path=str(tmp_path), purpose="project",
                                          min_free_bytes=1, min_free_inodes=1)], model_files=[]))

                native.skill(
                    'progn(ddCreateLib("captureTech" ' + json.dumps(str(tmp_path / "captureTech"))
                    + ') ocTech=techOpenTechFile("captureTech" "tech.db" "w") techSaveTechFile(ocTech) '
                    'ocLibraryRead=getd(\'aiLibraryObservation) '
                    'procedure(ocLibraryProbe(oclName) printf("LIBRARY_READ\\n") '
                    'apply(ocLibraryRead list(oclName))) putd(\'aiLibraryObservation getd(\'ocLibraryProbe)) '
                    'ocSymbolBuild=getd(\'aiTsBuild) '
                    'procedure(ocSymbolProbe(ocsCv ocsEntry) printf("SYMBOL_BUILD\\n") '
                    'warn("SYMBOL_WARNING\\n") warn("BUFFERED\\n") '
                    'apply(ocSymbolBuild list(ocsCv ocsEntry))) putd(\'aiTsBuild getd(\'ocSymbolProbe)) '
                    'ocConfigRead=getd(\'aiConfigRead) '
                    'procedure(ocConfigProbe(occLib occCell occView) printf("CONFIG_READ\\n") '
                    'fprintf(errport "CONFIG_ERROR_PORT\\n") '
                    'apply(ocConfigRead list(occLib occCell occView))) '
                    'putd(\'aiConfigRead getd(\'ocConfigProbe)) '
                    'ocBindingRead=getd(\'aiTsBindingData) '
                    'procedure(ocBindingProbe(ocbEntry) printf("SYMBOL_BINDING\\n") '
                    'apply(ocBindingRead list(ocbEntry))) putd(\'aiTsBindingData getd(\'ocBindingProbe)) '
                    'ocInspectRead=getd(\'aiInspectSchematic) '
                    'procedure(ocInspectProbe(@rest ociArgs) printf("INSPECT_OUTPUT\\n") '
                    'warn("INSPECT_WARNING\\n") warn("BUFFERED\\n") '
                    'apply(ocInspectRead ociArgs)) putd(\'aiInspectSchematic getd(\'ocInspectProbe)) '
                    'ocExtensionRead=getd(\'aiCrListExtensions) '
                    'procedure(ocExtensionProbe(@rest oceArgs) printf("EXTENSION_OUTPUT\\n") '
                    'apply(ocExtensionRead oceArgs)) putd(\'aiCrListExtensions getd(\'ocExtensionProbe)) t)'
                )
                extensions = call("list_project_extensions", dict(
                    target=dict(library="analogLib", cell="res", view="symbol"),
                    library_path=native.skill('ddGetObjReadPath(ddGetObj("analogLib"))')))
                assert extensions["output_capture"] == "skill_ports"
                assert "EXTENSION_OUTPUT" in extensions["output"]
                call("begin_circuit_task", dict(task_id="library", presentation="background",
                                                choice_confirmed=True))
                prepared_library = call("prepare_circuit_library", dict(task_ref="task:library",
                    request_id="library", library="captureWork", technology_library="captureTech"))
                assert prepared_library["output_capture"] == "skill_ports"
                library_args = dict(operation="create_circuit_library", arguments=dict(
                    prepare_ref=prepared_library["prepare_ref"], request_id="library"))
                library_result = call("execute_circuit_operation", library_args)
                assert library_result["receipt"]["saved"] and library_result["receipt"]["created"]
                assert "LIBRARY_READ" in library_result["last_response"]["output"]
                assert call("execute_circuit_operation", library_args)["last_response"] == library_result["last_response"]

                for mode in ("foreground", "background"):
                    task = call("begin_circuit_task", dict(
                        task_id=mode, presentation=mode, choice_confirmed=True))
                    observed = call("inspect_circuit_target", dict(task_ref=task["task_ref"],
                        request_id="inspect_" + mode,
                        target=dict(library="genericWork", cell=mode, view="schematic")))
                    assert observed["output_capture"] == "skill_ports"
                    creation = dict(fixture(masters, mode), task_ref=task["task_ref"], request_id=mode)
                    prepared = call("prepare_circuit_creation", creation)
                    assert prepared["output_capture"] == "skill_ports"
                    preflight(mode, mode)
                    args = dict(operation="create_circuit_from_plan", arguments=dict(
                        prepare_ref=prepared["prepare_ref"],
                        geometry_plan_digest=creation["geometry_plan_digest"], request_id=mode))
                    created = call("execute_circuit_operation", args)
                    receipt, captured = created["receipt"], created["last_response"]
                    assert captured["svg_preview"]["status"] == "ready", captured["svg_preview"]
                    assert captured["svg_preview"]["artifact"]["path"].endswith(".svg")
                    assert receipt["saved"] and receipt["readback_verified"] and receipt["check"] == [0, 0]
                    assert receipt["window_open"] is (mode == "foreground")
                    assert captured["output_capture"] == "skill_ports" and not captured["output_truncated"]
                    assert all(marker in captured["output"] for marker in
                               ("BUILD_OUTPUT", "BUILD_WARNING", "BUILD_ERROR_PORT", "SCH-1170"))
                    repeat = call("execute_circuit_operation", args)
                    assert repeat["receipt"] == receipt and repeat["last_response"] == captured
                    offline = call("get_circuit_operation", dict(
                        operation="create_circuit_from_plan", request_id=mode, refresh=False))
                    assert offline["last_response"] == captured
                    inspected = call("inspect_created_circuit", dict(circuit_ref=receipt["circuit_ref"]))
                    assert inspected["output_capture"] == "skill_ports"
                    assert "BUILD_OUTPUT" not in inspected["output"]
                    general = call("inspect_schematic", dict(lib="genericWork", cell=mode))
                    assert general["output_capture"] == "skill_ports"
                    assert "INSPECT_OUTPUT" in general["output"] and "INSPECT_WARNING" in general["output"]
                    capture_path = tmp_path / (mode + "-template.jsonl")
                    native.skill('aiTplCapture("genericWork" ' + json.dumps(mode)
                        + ' "schematic" "symbol" "layout" ' + json.dumps(str(capture_path))
                        + ' list("schematic"))')
                    template = make_template(read_capture(capture_path))
                    mode_catalog = catalog / mode
                    mode_catalog.mkdir()
                    publish([template], mode_catalog / "catalog.sqlite3")
                    monkeypatch.setenv("SICO_CIRCUIT_TEMPLATES_DIR", str(mode_catalog))
                    native.skill('let((ocsSource ocsCopy) '
                        'ocsSource=dbOpenCellViewByType("genericWork" ' + json.dumps(mode)
                        + ' "schematic" "" "r") '
                        'ocsCopy=dbCopyCellView(ocsSource "captureWork" ' + json.dumps(mode)
                        + ' "schematic") dbSave(ocsCopy) dbClose(ocsCopy) dbClose(ocsSource) t)')
                    symbol_preview = call("preview_template_symbol", dict(library="captureWork",
                        cell=mode, template_ref=template["template_ref"], task_ref=task["task_ref"],
                        request_id=mode))
                    assert symbol_preview["output_capture"] == "skill_ports"
                    preflight(mode, mode, "symbol", library="captureWork")
                    symbol_args = dict(operation="create_template_symbol", arguments=dict(
                        preview_ref=symbol_preview["preview_ref"],
                        preview_digest=symbol_preview["preview_digest"], request_id=mode))
                    symbol = call("execute_circuit_operation", symbol_args)
                    assert symbol["receipt"]["saved"] and symbol["receipt"]["readback_verified"]
                    assert symbol["receipt"]["interface_check"] == [0, 0]
                    assert "SYMBOL_BUILD" in symbol["last_response"]["output"]
                    assert "SYMBOL_WARNING" in symbol["last_response"]["output"]
                    assert call("execute_circuit_operation", symbol_args)["last_response"] == symbol["last_response"]
                    symbol_ref = symbol["receipt"]["symbol_ref"]
                    assert call("inspect_template_symbol", dict(symbol_ref=symbol_ref))["output_capture"] == "skill_ports"
                    binding = call("get_symbol_binding", dict(symbol_ref=symbol_ref,
                        master_id="design", instance_id="dut"))
                    assert binding["output_capture"] == "skill_ports" and "SYMBOL_BINDING" in binding["output"]

                    preflight(mode, mode, "config")
                    config_args = dict(operation="create_circuit_config", arguments=dict(
                        library="genericWork", cell=mode, view="config", top_library="genericWork",
                        top_cell=mode, task_ref=task["task_ref"], request_id=mode, bindings=[],
                        switch_views=["spectre", "schematic"], stop_views=["spectre"]))
                    config = call("execute_circuit_operation", config_args)
                    assert config["receipt"]["saved"] and config["receipt"]["bindings"]
                    assert all(marker in config["last_response"]["output"] for marker in
                               ("CONFIG_READ", "CONFIG_ERROR_PORT"))
                    assert call("execute_circuit_operation", config_args)["last_response"] == config["last_response"]
                    assert call("inspect_circuit_config", dict(config_ref=config["receipt"]["config_ref"]))["output_capture"] == "skill_ports"
                    assert native.skill("and(poport==ocOriginalPo woport==ocOriginalWo "
                                        "errport==ocOriginalErr openportp(ocOriginalPo) "
                                        "openportp(ocOriginalWo) openportp(ocOriginalErr))") == "t"

                # First ADE load happens after both captured creation requests.
                load_backend(native, tmp_path)
                source_fixture(native, server)
                (tmp_path / "simulation").mkdir(exist_ok=True)
                task = call("begin_circuit_task", dict(
                    task_id="simulation", presentation="foreground", choice_confirmed=True))
                spec = recipe(cell="later_maestro")
                preview = call("preview_simulation_recipe", dict(recipe=spec))
                preflight("later_maestro", "foreground", "maestro")
                setup = call("execute_circuit_operation", dict(operation="create_simulation_setup",
                    arguments=dict(recipe=spec, recipe_digest=preview["recipe_digest"],
                                   task_ref=task["task_ref"], request_id="setup")))
                assert setup["receipt"]["saved"] and setup["receipt"]["readback_verified"]
                assert "output_capture" not in setup["last_response"]
                call("preflight_circuit_project", dict(presentation="foreground",
                    targets=[dict(library="genericWork", cell="later_maestro", view="maestro",
                                  oa_view_type="maestro", intent="read")],
                    libraries=[dict(library="genericWork", expected_path=str(tmp_path / "library"))],
                    directories=[dict(path=str(tmp_path), purpose="project", min_free_bytes=1,
                                      min_free_inodes=1), dict(path=str(tmp_path / "simulation"),
                                      purpose="run", min_free_bytes=1, min_free_inodes=1)],
                    model_files=[], simulator_executable=shutil.which("spectre")))
                run = call("execute_circuit_operation", dict(operation="run_simulation_setup",
                    arguments=dict(setup_ref=setup["receipt"]["setup_ref"], request_id="run")))
                assert "output_capture" not in run["last_response"]
                deadline = time.monotonic() + 120
                while True:
                    status = call("get_simulation_recipe_status", dict(run_ref=run["receipt"]["run_ref"]))
                    if status["state"] != "running":
                        break
                    assert time.monotonic() < deadline, status
                    time.sleep(.5)
                assert status["state"] == "completed", status
                assert process.poll() is None
            finally:
                (tmp_path / "capture-evidence.json").write_text(json.dumps(evidence, indent=2))
                if process.poll() is None:
                    native.skill("progn(sicoDisconnect(ocCid) t)")
