from __future__ import annotations

import json
from pathlib import Path

import pytest

from aivworkbench.executor import ExecutorContext, ExecutorResult, execute_workflow
from aivworkbench.executors import cadence_si
from aivworkbench.executors.cadence_si import run_cadence_si
from aivworkbench.recipe import load_builtin_recipe
from aivworkbench.registry import builtin_registry
from aivworkbench.workspace import (
    allocate_split_run,
    resolve_launch_paths,
    stable_digest,
)


_SOURCE_GENERATION = "a" * 64


@pytest.fixture(autouse=True)
def _stable_post_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(
        cadence_si,
        "_post_snapshot",
        lambda _context: ExecutorResult(
            "PASS", outputs={"source_generation": _SOURCE_GENERATION}
        ),
    )


def _run(tmp_path: Path):
    launch = tmp_path / "launch"
    payload = tmp_path / "payload"
    launch.mkdir()
    payload.mkdir()
    return allocate_split_run(
        resolve_launch_paths(launch),
        payload,
        library="saradc",
        cell="comparator_new",
        view="rnm",
        kind="cadence-si-test",
        request_digest=stable_digest({"cadence_si": "test"}),
    )


def _snapshot(
    context: ExecutorContext, *, source_generation: str | None = _SOURCE_GENERATION
):
    outputs = {}
    if source_generation is not None:
        outputs["source_generation"] = source_generation
    return ExecutorResult("PASS", outputs=outputs)


def _script(
    path: Path,
    *,
    diagnostic: str = "",
    make_map: bool = True,
    make_globalmap: bool = True,
    netlist_text: str = "module comparator_new; endmodule\n",
    success: bool = True,
    exit_code: int = 0,
    sleep_seconds: int = 0,
    environment_probe: Path | None = None,
    globalmap_target: str = "saradc/comparator_new/schematic comparator_new",
    mutate_si_env: bool = False,
    writeback_key: str | None = None,
    duplicate_writeback_key: str | None = None,
) -> Path:
    lines = ["#!/bin/sh", "set -eu"]
    if sleep_seconds:
        lines.append(f"/bin/sleep {sleep_seconds}")
    if environment_probe is not None:
        lines.extend(
            [
                "printf '%s\\n' \"${PROJECT-}\" > "
                + _shell_single_quote(str(environment_probe)),
                'if [ -n "${SICO_AI_TOKEN-}" ] || [ -n "${CAD_AI_TOKEN-}" ] || [ -n "${CAD_CODEX_TOKEN-}" ]; then',
                "  printf '%s\\n' 'ERROR: controller credential reached si'",
                "  exit 89",
                "fi",
            ]
        )
    lines.extend(
        [
            "mkdir -p ihnl/cds0 map",
            "printf '%s' " + _shell_single_quote(netlist_text) + " > ihnl/cds0/netlist",
        ]
    )
    if make_map:
        lines.append("printf '%s\n' '(map current)' > map/current")
    if make_globalmap:
        lines.append(
            "printf '%s\n' 'CADENCE IHNL11140' "
            + _shell_single_quote(globalmap_target)
            + " > ihnl/globalmap"
        )
    if mutate_si_env:
        lines.append("printf '%s\n' 'simLibName = \"changed\"' > si.env")
    if writeback_key is not None:
        lines.append("printf '%s\n' '" + writeback_key + " = nil' >> si.env")
    if duplicate_writeback_key is not None:
        lines.append("printf '%s\\n' '" + duplicate_writeback_key + " = nil' >> si.env")
    if diagnostic:
        lines.append("printf '%s\n' " + _shell_single_quote(diagnostic))
    if success:
        lines.append("printf '%s\n' 'End netlisting Aug 30 08:00:00 2026'")
    lines.append(f"exit {exit_code}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _shell_single_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _execute(
    tmp_path: Path,
    si: Path,
    *,
    source_generation: str | None = _SOURCE_GENERATION,
    cds_lib: Path | None = None,
    workspace_root: Path | None = None,
    project_environment: dict[str, str] | None = None,
    environment: dict[str, str] | None = None,
    timeout: float = 10.0,
):
    approved = tmp_path / "approved"
    approved.mkdir(exist_ok=True)
    selected_cds_lib = cds_lib or approved / "cds.lib"
    if not selected_cds_lib.exists():
        selected_cds_lib.parent.mkdir(parents=True, exist_ok=True)
        selected_cds_lib.write_text(
            "DEFINE saradc /qualified/oa/saradc\n", encoding="utf-8"
        )
    root = workspace_root or approved

    def snapshot(context: ExecutorContext) -> ExecutorResult:
        return _snapshot(context, source_generation=source_generation)

    registry = builtin_registry(
        executor_handlers={"virtuoso.snapshot": snapshot, "cadence.si": run_cadence_si}
    )
    return execute_workflow(
        load_builtin_recipe("saradc.comparator_new"),
        registry,
        _run(tmp_path),
        tools={"si": str(si)},
        environment=environment,
        timeout=timeout,
        targets=("structure",),
        metadata={
            "project": "test",
            "project_root": str(root),
            "workspace_root": str(root),
            "cds_lib": str(selected_cds_lib),
            "project_environment": project_environment or {},
        },
    )


def test_cadence_si_passes_only_with_complete_authoritative_inventory(
    tmp_path: Path,
) -> None:
    result = _execute(tmp_path, _script(tmp_path / "si"))

    structure = result.gates["structure"]
    assert result.status == "PASS"
    assert structure.status == "PASS"
    assert structure.outputs["structure_authoritative"] is True
    assert structure.outputs["source_generation"] == _SOURCE_GENERATION
    assert len(structure.outputs["netlist"]) == 1
    assert structure.outputs["map"].endswith("/run/map/current")
    assert structure.outputs["globalmap"].endswith("/run/ihnl/globalmap")
    inventory_path = next(
        path for path in structure.artifacts if path.name == "structure-inventory.json"
    )
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    assert inventory["complete"] is True
    assert inventory["target_present_in_globalmap"] is True
    assert {record["path"] for record in inventory["records"]} >= {
        structure.outputs["map"],
        structure.outputs["globalmap"],
    }
    si_env = next(path for path in structure.artifacts if path.parent.name == "input")
    assert si_env.read_text(encoding="utf-8") == (
        'simLibName = "saradc"\n'
        'simCellName = "comparator_new"\n'
        'simViewName = "schematic"\n'
        'simSimulator = "verilog"\n'
        "simNetlistHier = t\n"
    )


def test_project_environment_is_injected_but_controller_credentials_are_not(
    tmp_path: Path,
) -> None:
    approved = tmp_path / "approved"
    approved.mkdir()
    probe = tmp_path / "environment.txt"
    result = _execute(
        tmp_path,
        _script(tmp_path / "si", environment_probe=probe),
        workspace_root=approved,
        project_environment={"PROJECT": str(approved)},
        environment={
            "PATH": "/usr/bin:/bin",
            "CAD_AI_TOKEN": "must-not-reach-si",
            "SICO_AI_TOKEN": "must-not-reach-si",
            "CAD_CODEX_TOKEN": "must-not-reach-si",
        },
    )

    assert result.gates["structure"].status == "PASS"
    assert probe.read_text(encoding="utf-8").strip() == str(approved)


@pytest.mark.parametrize(
    "diagnostic",
    (
        "ERROR (VLOGNET-40): failed to netlist the selected design",
        "ERROR (OSSHNL-514): the requested view could not be opened",
    ),
)
def test_zero_return_code_with_cadence_error_diagnostic_fails(
    diagnostic: str, tmp_path: Path
) -> None:
    result = _execute(tmp_path, _script(tmp_path / "si", diagnostic=diagnostic))

    structure = result.gates["structure"]
    assert result.status == "FAIL_WORKFLOW"
    assert structure.status == "FAIL_CADENCE_SI"
    assert structure.summary["code"] == "si_error_diagnostics"
    assert diagnostic in structure.summary["evidence"]["error_diagnostics"]


def test_zero_error_summary_text_does_not_create_a_false_failure(
    tmp_path: Path,
) -> None:
    result = _execute(
        tmp_path,
        _script(tmp_path / "si", diagnostic="INFO: netlisting completed with 0 errors"),
    )

    assert result.gates["structure"].status == "PASS"


@pytest.mark.parametrize(
    ("options", "missing"),
    (
        ({"make_globalmap": False}, "globalmap"),
        ({"make_map": False}, "map"),
        ({"netlist_text": ""}, "netlist"),
    ),
)
def test_missing_or_empty_required_structure_artifact_fails(
    options: dict[str, object], missing: str, tmp_path: Path
) -> None:
    result = _execute(tmp_path, _script(tmp_path / "si", **options))

    structure = result.gates["structure"]
    assert structure.status == "FAIL_CADENCE_SI"
    assert structure.summary["code"] == "required_structure_artifacts_missing"
    inventory = structure.summary["evidence"]
    assert inventory["status"] == "NOT_PASS"
    if missing == "netlist":
        assert structure.outputs["structure_authoritative"] is False


def test_success_marker_is_required_even_when_process_and_files_look_good(
    tmp_path: Path,
) -> None:
    result = _execute(tmp_path, _script(tmp_path / "si", success=False))

    assert result.gates["structure"].status == "FAIL_CADENCE_SI"
    assert result.gates["structure"].summary["code"] == "si_success_evidence_missing"


def test_globalmap_must_identify_the_requested_source_view(tmp_path: Path) -> None:
    result = _execute(
        tmp_path,
        _script(
            tmp_path / "si",
            globalmap_target="saradc/another_cell/schematic another_cell",
        ),
    )

    assert result.gates["structure"].status == "FAIL_CADENCE_SI"
    assert result.gates["structure"].summary["code"] == (
        "required_structure_artifacts_missing"
    )


def test_si_must_not_change_the_rendered_design_identity(tmp_path: Path) -> None:
    result = _execute(tmp_path, _script(tmp_path / "si", mutate_si_env=True))

    assert result.gates["structure"].status == "FAIL_CADENCE_SI"
    assert result.gates["structure"].summary["code"] == "si_env_identity_changed"


def test_cadence_runtime_si_env_writeback_is_allowed(tmp_path: Path) -> None:
    result = _execute(tmp_path, _script(tmp_path / "si", writeback_key="simViewList"))

    assert result.gates["structure"].status == "PASS"
    evidence = result.gates["structure"].summary["evidence"]
    assert evidence["si_env_unchanged"] is False
    assert evidence["si_env_identity_unchanged"] is True
    assert evidence["si_env_runtime_writeback_allowed"] is True
    assert "simViewList" in evidence["si_env_writeback"]["added_keys"]


def test_unknown_si_env_writeback_is_rejected(tmp_path: Path) -> None:
    result = _execute(
        tmp_path,
        _script(tmp_path / "si", writeback_key="AIVW_UNTRUSTED_SETTING"),
    )

    assert result.gates["structure"].status == "FAIL_CADENCE_SI"
    assert result.gates["structure"].summary["code"] == "si_env_writeback_not_allowed"


def test_identical_duplicate_runtime_key_is_allowed(tmp_path: Path) -> None:
    result = _execute(
        tmp_path,
        _script(
            tmp_path / "si",
            writeback_key="simViewList",
            duplicate_writeback_key="simViewList",
        ),
    )

    assert result.gates["structure"].status == "PASS"
    evidence = result.gates["structure"].summary["evidence"]
    assert evidence["si_env_writeback"]["active_duplicate_keys"] == ["simViewList"]


def test_source_generation_change_after_si_marks_structure_stale(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        cadence_si,
        "_post_snapshot",
        lambda _context: ExecutorResult(
            "PASS", outputs={"source_generation": "b" * 64}
        ),
    )

    result = _execute(tmp_path, _script(tmp_path / "si"))

    assert result.gates["structure"].status == "STALE_SOURCE"
    assert result.gates["structure"].summary["code"] == "source_generation_changed"
    assert result.gates["structure"].outputs["structure_authoritative"] is False


def test_post_snapshot_must_pass_before_structure_becomes_authoritative(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        cadence_si,
        "_post_snapshot",
        lambda _context: ExecutorResult(
            "BLOCKED_UNSAVED_SOURCE", {"code": "unsaved_or_unknown_source"}
        ),
    )

    result = _execute(tmp_path, _script(tmp_path / "si"))

    assert result.gates["structure"].status == "BLOCKED_UNSAVED_SOURCE"
    assert result.gates["structure"].summary["code"] == "post_snapshot_not_pass"
    assert result.gates["structure"].outputs["structure_authoritative"] is False


def test_snapshot_without_source_generation_blocks_si_before_process(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "started"
    si = tmp_path / "si"
    si.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    si.chmod(0o755)

    result = _execute(tmp_path, si, source_generation=None)

    assert result.gates["structure"].status == "BLOCKED_INPUT"
    assert result.gates["structure"].summary["code"] == "missing_source_generation"
    assert not marker.exists()


def test_cds_lib_must_remain_under_approved_workspace(tmp_path: Path) -> None:
    approved = tmp_path / "approved"
    approved.mkdir()
    outside = tmp_path / "outside" / "cds.lib"
    outside.parent.mkdir()
    outside.write_text("DEFINE saradc /wrong/oa\n", encoding="utf-8")

    result = _execute(
        tmp_path,
        _script(tmp_path / "si"),
        cds_lib=outside,
        workspace_root=approved,
    )

    assert result.gates["structure"].status == "BLOCKED_INPUT"
    assert result.gates["structure"].summary["code"] == "cds_lib_path_escape"


def test_smic28_cds_lib_is_rejected(tmp_path: Path) -> None:
    forbidden = tmp_path / "smic28" / "cds.lib"
    forbidden.parent.mkdir()
    forbidden.write_text("DEFINE saradc /forbidden/oa\n", encoding="utf-8")

    result = _execute(
        tmp_path,
        _script(tmp_path / "si"),
        cds_lib=forbidden,
        workspace_root=tmp_path,
    )

    assert result.gates["structure"].status == "BLOCKED_INPUT"
    assert result.gates["structure"].summary["code"] == "forbidden_workspace"


def test_si_timeout_is_distinct_from_netlisting_failure(tmp_path: Path) -> None:
    result = _execute(
        tmp_path,
        _script(tmp_path / "si", sleep_seconds=2),
        timeout=0.05,
    )

    assert result.gates["structure"].status == "BLOCKED_TIMEOUT"
    assert result.gates["structure"].summary["code"] == "si_timeout"


def test_nonzero_si_process_fails_even_if_artifacts_exist(tmp_path: Path) -> None:
    result = _execute(tmp_path, _script(tmp_path / "si", exit_code=7))

    assert result.gates["structure"].status == "FAIL_CADENCE_SI"
    assert result.gates["structure"].summary["code"] == "si_process_failed"
