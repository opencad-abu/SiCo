from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile

import pytest

from aivworkbench.executor import ExecutorContext
from aivworkbench.executors.xcelium import (
    run_rnm_check,
)
from aivworkbench.l1 import build_l1_test_plan
from aivworkbench.recipe import Recipe, load_builtin_recipe
from xcelium_evidence_fixtures import _adapter, _executor_context, _plan


def test_imc_contract_is_recognized_but_preflight_is_always_blocking(
    tmp_path: Path,
) -> None:
    adapter = _adapter()
    plan = _plan(backend="imc")
    binding = adapter.binding_for_plan(plan)
    assert adapter.validate_plan(plan, binding) == []
    assert adapter.preflight_tools({}, plan) == {
        "status": "BLOCKED_ENVIRONMENT",
        "code": "imc_unavailable",
        "reason": "IMC executable is unavailable; UCIS/IMC analysis is not qualified",
    }

    imc = tmp_path / "imc"
    imc.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    blocked = adapter.preflight_tools({"imc": str(imc)}, plan)
    assert blocked is not None
    assert blocked["status"] == "BLOCKED_ENVIRONMENT"
    assert blocked["code"] == "imc_analysis_unqualified"
    with pytest.raises(ValueError, match="only raw Xcelium UCIS"):
        adapter.prepare_simulation(tmp_path, tmp_path, plan, binding)


def test_executor_blocks_imc_before_binding_or_xrun_resolution(tmp_path: Path) -> None:
    context = _executor_context(tmp_path, backend="imc")
    generated = context.dependencies["generate"].outputs
    generated["xcelium_evidence_binding"]["coverage"]["producer"] = "spectre"
    result = run_rnm_check(context)
    assert result.status == "BLOCKED_ENVIRONMENT"
    assert result.summary["code"] == "imc_unavailable"
    assert list(context.gate_root.iterdir()) == []

    imc = tmp_path / "imc"
    imc.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    second_root = tmp_path / "second"
    second_root.mkdir()
    context = _executor_context(second_root, backend="imc", tools={"imc": str(imc)})
    result = run_rnm_check(context)
    assert result.status == "BLOCKED_ENVIRONMENT"
    assert result.summary["code"] == "imc_analysis_unqualified"


def test_executor_blocks_physical_pvt_without_registered_adapter_before_subprocess(
    tmp_path: Path,
) -> None:
    """The Xcelium comparator adapter must not impersonate a physical PVT run."""
    context = _executor_context(tmp_path)
    physical_pvt = {
        "schema_version": 1,
        "kind": "physical_pvt_exact_cross_product",
        "require_exact_cross_product": True,
        "dimensions": [
            {
                "name": "process",
                "source": "corner",
                "role": "process",
                "values": ["tt"],
            },
            {
                "name": "temperature_c",
                "source": "corner",
                "role": "temperature",
                "values": [27],
            },
            {
                "name": "vdd",
                "source": "corner",
                "role": "supply",
                "values": [1.2],
            },
            {
                "name": "vector",
                "source": "vector_id",
                "role": "vector",
                "values": ["positive", "negative", "tie"],
            },
        ],
        "model_binding": {
            "model_file": "models/gpdk045.scs",
            "section_dimension": "process",
            "section_by_value": {"tt": "tt"},
        },
        "execution": {
            "simulator": "spectre",
            "launcher": "spectre_pvt_v1",
            "raw_output": "psf",
            "payload_relative_root": "spectre/pvt",
        },
    }
    verification = deepcopy(dict(context.recipe.payload["verification"]))
    for case, vector_id in zip(verification["cases"], ("positive", "negative", "tie")):
        case["corner"] = {
            "process": "tt",
            "temperature_c": 27,
            "vdd": 1.2,
        }
        case["vector_id"] = vector_id
    verification["physical_pvt"] = physical_pvt
    recipe_payload = deepcopy(dict(context.recipe.payload))
    recipe_payload["verification"] = verification
    context = ExecutorContext(
        recipe=Recipe(
            context.recipe.path,
            recipe_payload,
            context.recipe.sha256,
            context.recipe.resolved_inputs,
        ),
        run=context.run,
        gate_id=context.gate_id,
        executor_name=context.executor_name,
        dependencies=context.dependencies,
        environment=context.environment,
        tools=context.tools,
        timeout=context.timeout,
        metadata=context.metadata,
    )
    generated = context.dependencies["generate"].outputs
    generated["l1_test_plan"] = build_l1_test_plan(verification)
    xrun = tmp_path / "xrun"
    calls = tmp_path / "xrun.calls"
    xrun.write_text(
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{calls}'\nexit 0\n",
        encoding="utf-8",
    )
    xrun.chmod(0o755)
    context = ExecutorContext(
        recipe=context.recipe,
        run=context.run,
        gate_id=context.gate_id,
        executor_name=context.executor_name,
        dependencies=context.dependencies,
        environment=context.environment,
        tools={"xrun": str(xrun)},
        timeout=context.timeout,
        metadata=context.metadata,
    )

    result = run_rnm_check(context)

    assert result.status == "BLOCKED_ENVIRONMENT"
    assert result.summary == {
        "status": "BLOCKED_ENVIRONMENT",
        "code": "physical_pvt_adapter_unavailable",
        "physical_pvt_execution": "not_invoked",
        "design_verdict": "NOT_ESTABLISHED",
    }
    assert not calls.exists()
    assert list(context.gate_root.iterdir()) == []


def test_executor_requires_recipe_selected_adapter_to_match_candidate(
    tmp_path: Path,
) -> None:
    context = _executor_context(tmp_path)
    context.dependencies["generate"].outputs["xcelium_evidence_adapter"] = (
        "other.adapter"
    )
    result = run_rnm_check(context)
    assert result.status == "BLOCKED_INPUT"
    assert "does not match recipe" in result.summary["reason"]
    assert list(context.gate_root.iterdir()) == []


@pytest.mark.parametrize("kind", ("coverage", "waveform"))
def test_adapter_preflight_rejects_preexisting_bound_output(
    tmp_path: Path, kind: str
) -> None:
    adapter = _adapter()
    plan = _plan()
    relative = "evidence/coverage" if kind == "coverage" else "evidence/waves.shm"
    output = tmp_path / relative
    output.mkdir(parents=True)

    findings = adapter.preflight_outputs(tmp_path, plan)

    assert findings == [
        {
            "code": "xcelium_evidence_output_stale",
            "type": kind,
            "path": relative,
            "reason": "bound evidence path already exists before Xcelium invocation",
        }
    ]


def test_adapter_preflight_rejects_stale_symlink_even_when_target_is_missing(
    tmp_path: Path,
) -> None:
    adapter = _adapter()
    plan = _plan()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "coverage").symlink_to(
        tmp_path / "removed-target", target_is_directory=True
    )

    findings = adapter.preflight_outputs(tmp_path, plan)

    assert {item["type"] for item in findings} == {"coverage"}
    assert findings[0]["code"] == "xcelium_evidence_output_stale"


def test_adapter_preflight_rejects_existing_file_and_complete_old_evidence(
    tmp_path: Path,
) -> None:
    adapter = _adapter()
    plan = _plan()
    coverage = tmp_path / "evidence" / "coverage"
    coverage.mkdir(parents=True)
    (coverage / "old.ucm").write_text("old ucm\n", encoding="utf-8")
    (coverage / "old.ucd").write_text("old ucd\n", encoding="utf-8")
    waves = tmp_path / "evidence" / "waves.shm"
    waves.mkdir()
    (waves / "old.dsn").write_text("old dsn\n", encoding="utf-8")
    (waves / "old.trn").write_text("old trn\n", encoding="utf-8")

    findings = adapter.preflight_outputs(tmp_path, plan)

    assert {item["type"] for item in findings} == {"coverage", "waveform"}
    assert all(item["code"] == "xcelium_evidence_output_stale" for item in findings)


def test_adapter_preflight_rejects_file_at_bound_path(tmp_path: Path) -> None:
    adapter = _adapter()
    plan = _plan()
    bound = tmp_path / "evidence" / "coverage"
    bound.parent.mkdir(parents=True)
    bound.write_text("old coverage path\n", encoding="utf-8")

    findings = adapter.preflight_outputs(tmp_path, plan)

    assert findings[0]["type"] == "coverage"
    assert findings[0]["code"] == "xcelium_evidence_output_stale"


def test_executor_blocks_stale_adapter_output_before_any_subprocess(
    tmp_path: Path,
) -> None:
    xrun = tmp_path / "xrun"
    calls = tmp_path / "xrun.calls"
    xrun.write_text(
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{calls}'\nexit 0\n",
        encoding="utf-8",
    )
    xrun.chmod(0o755)
    context = _executor_context(tmp_path, tools={"xrun": str(xrun)})
    stale = context.run.payload_root / "evidence" / "coverage"
    stale.mkdir(parents=True)

    result = run_rnm_check(context)

    assert result.status == "STALE_ARTIFACT"
    assert result.summary["code"] == "xcelium_evidence_output_stale"
    assert not calls.exists()
    assert list(context.gate_root.iterdir()) == []


def test_executor_classifies_dangling_stale_output_before_binding_validation(
    tmp_path: Path,
) -> None:
    xrun = tmp_path / "xrun"
    xrun.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    xrun.chmod(0o755)
    context = _executor_context(tmp_path, tools={"xrun": str(xrun)})
    evidence = context.run.payload_root / "evidence"
    evidence.mkdir(parents=True)
    (evidence / "coverage").symlink_to(
        context.run.payload_root / "removed-coverage", target_is_directory=True
    )
    context.dependencies["generate"].outputs["xcelium_evidence_binding"]["coverage"][
        "producer"
    ] = "spectre"

    result = run_rnm_check(context)

    assert result.status == "STALE_ARTIFACT"
    assert result.summary["code"] == "xcelium_evidence_output_stale"


@pytest.mark.parametrize("retention", ("always", "on_failure", "never"))
def test_adapter_controls_xcelium_arguments_and_retention_hooks(
    tmp_path: Path, retention: str
) -> None:
    adapter = _adapter()
    plan = _plan(retention=retention)
    payload = tmp_path / "payload"
    smoke = payload / "checks" / "smoke"
    smoke.mkdir(parents=True)
    preparation = adapter.prepare_simulation(
        payload, smoke, plan, adapter.binding_for_plan(plan)
    )
    coverage_path = payload / "evidence" / "coverage"
    assert preparation.compile_args == ("-coverage", "U")
    assert preparation.simulation_args[:6] == (
        "-coverage",
        "U",
        "-covoverwrite",
        "-covworkdir",
        str(coverage_path),
        "-covtest",
    )
    assert preparation.metadata["coverage_analysis"] == "not_run_imc_merge"
    assert preparation.metadata["qualification_scope"] == (
        "single comparator raw-evidence adapter qualification"
    )
    assert preparation.metadata["matrix_execution_scope"] is None
    hooks = adapter.render_hooks(plan)
    assert hooks.summary_fields["coverage_count"] == 1
    assert hooks.summary_fields["waveform_count"] == (1 if retention == "always" else 0)

    if retention == "always":
        assert len(preparation.artifacts) == 1
        tcl = preparation.artifacts[0].read_text(encoding="utf-8")
        assert "database -open waves" in tcl
        assert "probe -create" in tcl
        assert tcl.endswith("run\n")
        assert preparation.simulation_args[-2:] == (
            "-input",
            str(preparation.artifacts[0]),
        )
        assert '\\"event\\":\\"waveform\\"' in hooks.before_summary
    elif retention == "on_failure":
        assert preparation.artifacts == ()
        assert "$shm_open" in hooks.before_summary
        assert "if (aivw_evidence_failed)" in hooks.before_summary
    else:
        assert preparation.artifacts == ()
        assert "$shm_open" not in hooks.before_summary
        assert '\\"event\\":\\"waveform\\"' not in hooks.before_summary


def test_matrix_preparation_records_metadata_and_parameter_execution_scope() -> None:
    recipe = load_builtin_recipe("qualification.comparator_raw_evidence_matrix")
    plan = build_l1_test_plan(recipe.payload["verification"])
    adapter = _adapter()

    with tempfile.TemporaryDirectory() as directory:
        payload = Path(directory) / "payload"
        smoke = payload / "checks" / "smoke"
        smoke.mkdir(parents=True)
        preparation = adapter.prepare_simulation(
            payload, smoke, plan, adapter.binding_for_plan(plan)
        )

    assert preparation.metadata["qualification_scope"] == (
        "single comparator raw-evidence adapter qualification"
    )
    execution_scope = preparation.metadata["matrix_execution_scope"]
    assert execution_scope["kind"] == "recipe_owned_exact_cross_product"
    assert execution_scope["expected_point_count"] == 16
    assert execution_scope["physical_pvt_execution"] == "not_invoked"
    execution_by_name = {item["name"]: item for item in execution_scope["dimensions"]}
    assert execution_by_name["process"]["execution"] == "case_metadata_only"
    assert execution_by_name["temperature_c"]["execution"] == "case_metadata_only"
    assert execution_by_name["vector"]["execution"] == "case_metadata_only"
    assert execution_by_name["vdd_high"]["execution"] == (
        "controlled_sv_parameter_override"
    )
    assert execution_by_name["vdd_high"]["sv_parameter"] == "VDD_HIGH"
