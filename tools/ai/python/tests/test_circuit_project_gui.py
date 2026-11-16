"""Foreground acceptance: actual lost responses, retained partials and exact runs."""

import json
import os
import shutil
from copy import deepcopy

import pytest
from circuit_create_support import AI_ROOT, sample_master
from simulation_recipe_fixture import recipe
from template_smic28_support import migration_session
from template_symbol_fixture import scratch_schematic
from test_circuit_create_gui import prepare
from test_circuit_rc_gui import mcp_call
from test_draw_symbol import gate_record
from test_simulation_recipe_failures_gui import terminal
from test_simulation_recipe_gui import load_backend, source_fixture

from cadai.mcp import McpServer

pytestmark = pytest.mark.skipif(
    os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1",
    reason="licensed foreground project lifecycle acceptance",
)


class DropReply:
    def __init__(self, client, needle):
        self.client, self.needle, self.dropped = client, needle, False

    def call(self, method, args):
        response = self.client.call(method, args)
        if not self.dropped and self.needle in args.get("code", ""):
            self.dropped = True
            raise ConnectionError("injected lost reply after actual native dispatch")
        return response


def execute(server, operation, arguments):
    return mcp_call(
        server, "execute_circuit_operation", dict(operation=operation, arguments=arguments)
    )


def test_project_preflight_and_interruption_recovery(tmp_path):
    with migration_session(tmp_path) as (b, s, proc):
        for module in ["AI_circuit_project.il", "AI_circuit_config.il"]:
            b.skill("load(" + json.dumps(str(AI_ROOT / "skill" / module)) + ")")
        b.skill(
            'unless(ddGetObj("genericWork") ddCreateLib("genericWork" '
            + json.dumps(str(tmp_path / "library"))
            + "))"
        )
        load_backend(b, tmp_path)
        source_fixture(b, s)
        observations = {}
        requirements = {
            "presentation": "foreground",
            "targets": [
                {
                    "library": "genericWork",
                    "cell": "network",
                    "view": "schematic",
                    "oa_view_type": "schematic",
                    "intent": "read",
                }
            ],
            "libraries": [{"library": "genericWork", "expected_path": str(tmp_path / "library")}],
            "directories": [
                {
                    "path": str(tmp_path),
                    "purpose": "run",
                    "min_free_bytes": 1024,
                    "min_free_inodes": 1,
                }
            ],
            "model_files": [],
            "simulator_executable": shutil.which("spectre"),
        }
        good = mcp_call(s, "preflight_circuit_project", requirements)
        observations["preflight"] = good
        assert good["checks_passed"], good
        bad = mcp_call(
            s,
            "preflight_circuit_project",
            {**requirements, "model_files": [str(tmp_path / "missing.scs")]},
        )
        assert not bad["checks_passed"]
        observations["missing_model"] = bad
        b.skill(
            'progn(projectUserCv=dbOpenCellViewByType("genericWork" "network" "schematic" "" "a") '
            'dbReplaceProp(projectUserCv "projectUserEdit" "string" "preserve"))'
        )
        modified = mcp_call(s, "preflight_circuit_project", requirements)
        assert not modified["checks_passed"]
        assert b.skill("dbIsCellViewModified(projectUserCv)") == "t"
        b.skill("progn(schCheck(projectUserCv) dbSave(projectUserCv) dbClose(projectUserCv) t)")
        observations["unsaved_source"] = modified
        mcp_call(
            s,
            "begin_circuit_task",
            dict(task_id="foreground", presentation="foreground", choice_confirmed=True),
        )
        data = {c: sample_master(b, c) for c in ("res", "cap")}
        circuit_args = prepare(s, data, "journal_dut")
        lost = McpServer(DropReply(s.client, "aiCreateExecute("), workspace=tmp_path)
        created = execute(lost, "create_circuit_from_plan", circuit_args)
        assert created["receipt"]["saved"] and "dispatch_diagnostic" in created, created
        observations["lost_schematic_reply"] = created
        direct = mcp_call(s, "create_circuit_from_plan", circuit_args)
        assert not direct["ok"] and direct["code"] == "request_conflict_or_limit", direct
        observations["mixed_direct_call_rejected"] = direct
        restart = McpServer(s.client, workspace=tmp_path)
        assert execute(restart, "create_circuit_from_plan", circuit_args)["receipt"]["saved"]
        partial_args = prepare(s, data, "journal_partial")
        b.skill(
            "progn(projectBuildSaved=getd('aiCrBuild) "
            'procedure(aiCrBuild(cv entry) error("injected partial creation")) t)'
        )
        try:
            partial = execute(s, "create_circuit_from_plan", partial_args)
        finally:
            b.skill("putd('aiCrBuild projectBuildSaved)")
        assert partial["state"] == "partial_or_failed" and partial["receipt"]["view_created"], (
            partial
        )
        assert not partial["receipt"]["saved"]
        assert (
            execute(restart, "create_circuit_from_plan", partial_args)["receipt"]
            == partial["receipt"]
        )
        observations["partial_schematic"] = partial
        config = execute(
            s,
            "create_circuit_config",
            dict(
                library="genericWork",
                cell="network",
                top_library="genericWork",
                top_cell="network",
                task_ref="task:source",
                request_id="config",
            ),
        )
        assert config["receipt"]["saved"], config
        observations["config"] = config
        for module in [
            "AI_templates.il",
            "AI_circuit_symbol.il",
            "AI_template_symbol_geometry.il",
            "AI_template_symbol_draw.il",
            "AI_template_symbol.il",
        ]:
            b.skill("load(" + json.dumps(str(AI_ROOT / "skill" / module)) + ")")
        record = gate_record()
        for operation, style in [
            ("create_template_symbol", "rectangular_module"),
            ("draw_symbol", "template_outline"),
        ]:
            local = deepcopy(record)
            scopes = {}
            for net in local["topology"]["nets"]:
                if net["is_global"]:
                    scopes[net["id"]] = "local"
                    net.update(is_global=False, source_name=net["source_name"].rstrip("!"))
            scratch_schematic(b, "genericWork", operation, local)
            preview = mcp_call(
                s,
                "preview_template_symbol",
                dict(
                    template_ref=record["template_ref"],
                    task_ref="task:source",
                    request_id=operation,
                    library="genericWork",
                    cell=operation,
                    style=style,
                    net_scope_map=scopes,
                    **(
                        {"port_roles": {"VDD": "power", "VSS": "ground"}}
                        if style == "template_outline"
                        else {}
                    ),
                ),
            )
            assert preview["ok"], preview
            symbol_args = {k: preview[k] for k in ("preview_ref", "preview_digest")}
            symbol_args["request_id"] = operation
            symbol = execute(s, operation, symbol_args)
            assert symbol["receipt"]["saved"] and symbol["receipt"]["readback_verified"], symbol
            assert execute(restart, operation, symbol_args)["receipt"] == symbol["receipt"]
            observations[operation] = symbol
        model = tmp_path / "explicit.scs"
        model.write_text(
            "simulator lang=spectre\nlibrary fixture\nsection tt\n"
            "parameters projectProof=1\nendsection tt\nendlibrary fixture\n"
        )
        spec = recipe(cell="project_setup", model=model)
        preview = mcp_call(s, "preview_simulation_recipe", {"recipe": spec})
        setup_args = dict(
            recipe=spec,
            recipe_digest=preview["recipe_digest"],
            task_ref="task:source",
            request_id="project_setup",
        )
        lost_setup = McpServer(DropReply(s.client, "aiSimCreate("), workspace=tmp_path)
        setup = execute(lost_setup, "create_simulation_setup", setup_args)
        assert setup["receipt"]["saved"] and "dispatch_diagnostic" in setup, setup
        observations["lost_setup_reply"] = setup
        run_args = dict(setup_ref=setup["receipt"]["setup_ref"], request_id="project_run")
        lost_run = McpServer(DropReply(s.client, "aiSimRun("), workspace=tmp_path)
        running = execute(lost_run, "run_simulation_setup", run_args)
        assert running["state"] in {"running", "completed"}, running
        done = terminal(s, running["receipt"])
        assert done["state"] == "completed", done
        observations["completed_run"] = done
        # Inject unknown-start state after an actual completed fixture run, never a real orphan job.
        b.skill('aiSimulationRuns["simulation-run:project_run"][\'state]="start_unknown"')
        try:
            unknown = execute(restart, "run_simulation_setup", run_args)
            assert unknown["state"] == "start_unknown", unknown
            assert not unknown["automatic_resume_allowed"]
            observations["unknown_start"] = unknown
        finally:
            b.skill('aiSimulationRuns["simulation-run:project_run"][\'state]="completed"')
        # Move only this owned text fixture. OA files are never manipulated on disk.
        model.rename(tmp_path / "explicit.scs.retained")
        recovered_setup = execute(restart, "create_simulation_setup", setup_args)
        recovered_run = execute(restart, "run_simulation_setup", run_args)
        assert recovered_setup["receipt"]["setup_ref"] == setup["receipt"]["setup_ref"]
        assert recovered_run["receipt"]["history"] == done["history"]
        observations["missing_model_recovery"] = recovered_run
        offline = mcp_call(
            restart,
            "get_circuit_operation",
            dict(operation="run_simulation_setup", request_id="project_run", refresh=False),
        )
        assert offline["last_observation"]["receipt"]["history"] == done["history"]
        observations["offline"] = offline
        # Simulate lost native generation without killing or replacing any user process.
        identity = b.skill("aiProjectIdentity")
        b.skill('aiProjectIdentity="injected-new-generation"')
        try:
            mismatch = execute(restart, "run_simulation_setup", run_args)
            assert mismatch["state"] == "session_mismatch"
            observations["generation_mismatch"] = mismatch
        finally:
            b.skill("aiProjectIdentity=" + json.dumps(identity))
        # A previously terminal history whose foreground session disappears is not resumable.
        b.skill(
            'progn(projectSavedSession=aiSimulationRuns["simulation-run:project_run"][\'session] '
            'aiSimulationRuns["simulation-run:project_run"][\'session]=nil t)'
        )
        try:
            context = execute(restart, "run_simulation_setup", run_args)
            assert context["state"] == "context_lost", context
            observations["lost_context"] = context
        finally:
            b.skill('aiSimulationRuns["simulation-run:project_run"][\'session]=projectSavedSession')
        (tmp_path / "project-recovery.json").write_text(json.dumps(observations, indent=2))
        assert proc.poll() is None
