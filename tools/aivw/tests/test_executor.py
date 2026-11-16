from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from aivworkbench.errors import ExecutorError
from aivworkbench.executor import (
    ExecutorContext,
    ExecutorResult,
    ExternalArtifactLocator,
    execute_workflow,
)
from aivworkbench.executors.xcelium import run_rnm_check
from aivworkbench.m1ai_inputs import load_contract
from aivworkbench.plugins.latched_dynamic_comparator import BaselineGenerationRequest
from aivworkbench.recipe import load_builtin_recipe
from aivworkbench.registry import builtin_registry
from aivworkbench.workspace import (
    allocate_split_run,
    resolve_launch_paths,
    stable_digest,
)


def _run(tmp_path: Path):
    launch = tmp_path / "launch"
    payload = tmp_path / "payload"
    launch.mkdir()
    payload.mkdir()
    return allocate_split_run(
        resolve_launch_paths(launch),
        payload,
        library="lib",
        cell="cell",
        view="rnm",
        kind="recipe",
        request_digest=stable_digest({"executor": "test"}),
    )


def test_workflow_state_machine_executes_handlers_in_dependency_order(
    tmp_path: Path,
) -> None:
    recipe = load_builtin_recipe("saradc.comparator_new")
    seen: list[str] = []
    dependency_ids: dict[str, tuple[str, ...]] = {}

    def handler(context: ExecutorContext) -> ExecutorResult:
        assert all(result.status == "PASS" for result in context.dependencies.values())
        seen.append(context.gate_id)
        dependency_ids[context.gate_id] = tuple(context.dependencies)
        artifact = context.gate_root / "evidence.txt"
        artifact.write_text(context.gate_id + "\n", encoding="utf-8")
        return ExecutorResult("PASS", {"gate": context.gate_id}, artifacts=(artifact,))

    names = [str(item["executor"]) for item in recipe.payload["workflow"]["gates"]]
    registry = builtin_registry(executor_handlers={name: handler for name in names})
    result = execute_workflow(recipe, registry, _run(tmp_path))

    assert result.status == "PASS"
    assert seen == [
        "snapshot",
        "structure",
        "golden",
        "generate",
        "connectivity",
        "model_check",
        "correlation",
        "publish",
    ]
    assert all(item.status == "PASS" for item in result.gates.values())
    assert len(result.artifacts) == 16
    assert dependency_ids["connectivity"] == ("structure", "generate")


def test_workflow_indexes_shared_artifacts_once(tmp_path: Path) -> None:
    recipe = load_builtin_recipe("saradc.comparator_new")

    def handler(context: ExecutorContext) -> ExecutorResult:
        artifact = context.gate_root / "evidence.txt"
        artifact.write_text(context.gate_id + "\n", encoding="utf-8")
        cited = tuple(
            cited_artifact
            for result in context.dependencies.values()
            for cited_artifact in result.artifacts
        )
        return ExecutorResult(
            "PASS", {"gate": context.gate_id}, artifacts=(artifact, *cited)
        )

    names = [str(item["executor"]) for item in recipe.payload["workflow"]["gates"]]
    registry = builtin_registry(executor_handlers={name: handler for name in names})
    result = execute_workflow(recipe, registry, _run(tmp_path))

    assert result.status == "PASS"
    assert len(result.artifacts) == len(set(result.artifacts)) == 16


def test_workflow_blocks_unavailable_executor_and_skips_dependents(
    tmp_path: Path,
) -> None:
    recipe = load_builtin_recipe("saradc.comparator_new")
    result = execute_workflow(recipe, builtin_registry(), _run(tmp_path))

    assert result.status == "BLOCKED_WORKFLOW"
    assert result.gates["snapshot"].status == "BLOCKED_ENVIRONMENT"
    assert result.gates["structure"].status == "SKIPPED_DEPENDENCY"
    assert result.gates["publish"].status == "SKIPPED_DEPENDENCY"


def test_workflow_target_executes_dependency_closure_not_independent_branch(
    tmp_path: Path,
) -> None:
    recipe = load_builtin_recipe("saradc.comparator_new")
    seen: list[str] = []

    def handler(context: ExecutorContext) -> ExecutorResult:
        seen.append(context.gate_id)
        return ExecutorResult("PASS")

    registry = builtin_registry(
        executor_handlers={
            str(item["executor"]): handler
            for item in recipe.payload["workflow"]["gates"]
        }
    )
    result = execute_workflow(
        recipe, registry, _run(tmp_path), targets=("model_check",)
    )

    assert result.status == "PASS"
    assert seen == ["snapshot", "structure", "generate", "connectivity", "model_check"]
    assert "golden" not in result.gates
    assert "correlation" not in result.gates
    assert "publish" not in result.gates


def test_connectivity_is_an_explicit_bounded_target(tmp_path: Path) -> None:
    recipe = load_builtin_recipe("saradc.comparator_new")
    seen: list[str] = []

    def handler(context: ExecutorContext) -> ExecutorResult:
        seen.append(context.gate_id)
        if context.gate_id == "generate":
            source = context.run.payload_root / "candidate.sv"
            source.write_text("module comparator_new (out, outb, cdsNet1, cdsNet0, clk); endmodule\n", encoding="utf-8")
            return ExecutorResult("PASS", outputs={"connectivity_source": "candidate.sv"}, artifacts=(source,))
        if context.gate_id == "connectivity":
            return ExecutorResult("PASS")
        return ExecutorResult("PASS")

    registry = builtin_registry(
        executor_handlers={
            str(item["executor"]): handler
            for item in recipe.payload["workflow"]["gates"]
        }
    )
    result = execute_workflow(recipe, registry, _run(tmp_path), targets=("connectivity",))
    assert result.status == "PASS"
    assert seen == ["snapshot", "structure", "generate", "connectivity"]


def test_executor_artifact_must_remain_in_payload(tmp_path: Path) -> None:
    recipe = load_builtin_recipe("saradc.comparator_new")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")

    def unsafe(_context: ExecutorContext) -> ExecutorResult:
        return ExecutorResult("PASS", artifacts=(outside,))

    registry = builtin_registry(executor_handlers={"virtuoso.snapshot": unsafe})
    with pytest.raises(ExecutorError, match="unsafe artifact"):
        execute_workflow(recipe, registry, _run(tmp_path))


def test_executor_locator_rejects_traversal_and_symlink_components(tmp_path: Path) -> None:
    recipe = load_builtin_recipe("saradc.comparator_new")
    run = _run(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    linked_parent = run.payload_root / "linked"
    linked_parent.symlink_to(outside, target_is_directory=True)

    def traversal(_context: ExecutorContext) -> ExecutorResult:
        return ExecutorResult(
            "PASS",
            artifact_locators=(ExternalArtifactLocator(Path("../outside"), "xcelium"),),
        )

    registry = builtin_registry(executor_handlers={"virtuoso.snapshot": traversal})
    with pytest.raises(ExecutorError, match="traversal"):
        execute_workflow(recipe, registry, run)

    linked_file = outside / "wave.shm"
    linked_file.mkdir()

    def symlinked(_context: ExecutorContext) -> ExecutorResult:
        return ExecutorResult(
            "PASS",
            artifact_locators=(ExternalArtifactLocator(linked_parent / "wave.shm", "xcelium"),),
        )

    registry = builtin_registry(executor_handlers={"virtuoso.snapshot": symlinked})
    with pytest.raises(ExecutorError, match="unsafe artifact locator"):
        execute_workflow(recipe, registry, run)


def test_workflow_indexes_external_file_and_directory_locators_once(tmp_path: Path) -> None:
    recipe = load_builtin_recipe("saradc.comparator_new")
    run = _run(tmp_path)
    shared_dir = run.payload_root / "xcelium" / "waves.shm"
    shared_dir.mkdir(parents=True)
    (shared_dir / "index").write_text("wave\n", encoding="utf-8")
    shared_file = run.payload_root / "xcelium" / "coverage.ucis"
    shared_file.write_text("ucis\n", encoding="utf-8")

    def handler(context: ExecutorContext) -> ExecutorResult:
        return ExecutorResult(
            "PASS",
            artifact_locators=(
                ExternalArtifactLocator(shared_dir, "xcelium.waveform", {"evidence_id": "waves"}),
                ExternalArtifactLocator(shared_file, "xcelium.coverage", {"evidence_id": "coverage"}),
            ),
        )

    names = [str(item["executor"]) for item in recipe.payload["workflow"]["gates"]]
    registry = builtin_registry(executor_handlers={name: handler for name in names})
    result = execute_workflow(recipe, registry, run)

    assert result.status == "PASS"
    assert len(result.artifact_locators) == 2
    assert result.gates["snapshot"].artifact_locators[0].path == shared_dir.resolve()
    model_check_locator = result.gates["model_check"].artifact_locators[0]
    assert model_check_locator.path == shared_dir.resolve()
    assert model_check_locator.producer == "xcelium.waveform"


def test_controller_credentials_are_capability_scoped_per_executor(
    tmp_path: Path,
) -> None:
    recipe = load_builtin_recipe("saradc.comparator_new")
    tokens = ("SICO_AI_TOKEN", "CAD_AI_TOKEN", "CAD_CODEX_TOKEN")

    def snapshot(context: ExecutorContext) -> ExecutorResult:
        assert all(context.environment[name] == "private" for name in tokens)
        return ExecutorResult("PASS")

    def structure(context: ExecutorContext) -> ExecutorResult:
        # cadence.si retains IPC only for its post-netlisting re-snapshot.
        assert all(context.environment[name] == "private" for name in tokens)
        return ExecutorResult("PASS")

    def generate(context: ExecutorContext) -> ExecutorResult:
        assert not set(tokens) & context.environment.keys()
        assert context.environment["PATH"] == "/qualified/bin"
        return ExecutorResult("PASS")

    registry = builtin_registry(
        executor_handlers={
            "virtuoso.snapshot": snapshot,
            "cadence.si": structure,
            "ai.generate_model": generate,
        }
    )
    result = execute_workflow(
        recipe,
        registry,
        _run(tmp_path),
        environment={
            "PATH": "/qualified/bin",
            **dict.fromkeys(tokens, "private"),
        },
        targets=("generate",),
    )

    assert result.status == "PASS"


def test_registered_xcelium_executor_consumes_model_class_outputs(
    tmp_path: Path,
) -> None:
    recipe = load_builtin_recipe("saradc.comparator_new")
    xrun = tmp_path / "xrun"
    xrun.write_text(
        "#!/bin/sh\n"
        "echo 'TOOL: xrun(64) 23.09-s002: Started'\n"
        "case \"$*\" in *smoke_tb.sv*)\n"
        "echo 'AIVW_L1_EVENT {\"event\":\"check\",\"id\":\"initial-state\",\"status\":\"PASS\"}'\n"
        "echo 'AIVW_L1_EVENT {\"event\":\"check\",\"id\":\"clock-hold\",\"status\":\"PASS\"}'\n"
        "echo 'AIVW_L1_EVENT {\"event\":\"case\",\"id\":\"nominal-positive\",\"type\":\"positive_differential\",\"region\":\"deterministic\",\"status\":\"PASS\",\"passed_assertions\":[\"decision\",\"out\",\"outb\",\"complementary\"],\"metrics\":{\"decision\":\"positive\",\"out\":1.2,\"outb\":0.0}}'\n"
        "echo 'AIVW_L1_EVENT {\"event\":\"case\",\"id\":\"nominal-negative\",\"type\":\"negative_differential\",\"region\":\"deterministic\",\"status\":\"PASS\",\"passed_assertions\":[\"decision\",\"out\",\"outb\",\"complementary\"],\"metrics\":{\"decision\":\"negative\",\"out\":0.0,\"outb\":1.2}}'\n"
        "echo 'AIVW_L1_EVENT {\"event\":\"case\",\"id\":\"nominal-zero\",\"type\":\"zero_differential\",\"region\":\"indeterminate\",\"status\":\"PASS\",\"passed_assertions\":[\"decision\",\"out\",\"outb\",\"complementary\"],\"metrics\":{\"decision\":\"tie\",\"out\":0.6,\"outb\":0.6}}'\n"
        "echo 'AIVW_L1_EVENT {\"event\":\"summary\",\"status\":\"PASS\",\"case_count\":3,\"check_count\":2,\"assertion_count\":12}'\n"
        "echo 'AIVW_RNM_SMOKE checks=5 PASS'\n"
        ";; esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    xrun.chmod(0o755)
    registry = builtin_registry(executor_handlers={})

    def pass_handler(_context: ExecutorContext) -> ExecutorResult:
        return ExecutorResult("PASS")

    def generate(context: ExecutorContext) -> ExecutorResult:
        contract_path = context.recipe.input_path("interface_contract")
        spec_path = context.recipe.input_path("behavior_spec")
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        plugin = registry.require_model_class(context.recipe.model_class).handler
        assert plugin is not None
        candidate = plugin(
            BaselineGenerationRequest(
                contract=load_contract(contract_path),
                spec=spec,
                contract_sha256="contract",
                spec_sha256="spec",
                generated_at="test",
                verification=context.recipe.payload["verification"],
            )
        )
        model = context.run.payload_root / "models" / "candidate.rnm.sv"
        tb = context.run.payload_root / "xcelium" / "candidate_smoke_tb.sv"
        model.parent.mkdir()
        tb.parent.mkdir()
        model.write_text(candidate.source, encoding="utf-8")
        tb.write_text(candidate.smoke_testbench, encoding="utf-8")
        return ExecutorResult(
            "PASS",
            outputs={
                "model_source": model.relative_to(context.run.payload_root).as_posix(),
                "smoke_testbench": tb.relative_to(context.run.payload_root).as_posix(),
                "pass_marker": "AIVW_RNM_SMOKE checks=5 PASS",
                "l1_test_plan": candidate.test_plan,
            },
            artifacts=(model, tb),
        )

    for name in (
        "virtuoso.snapshot",
        "cadence.si",
        "spectre.golden_evidence",
        "evidence.correlate",
        "virtuoso.publish_text_view",
    ):
        registry.bind_executor(name, pass_handler)
    registry.bind_executor("ai.generate_model", generate)
    registry.bind_executor("connectivity.check", pass_handler)
    registry.bind_executor("xcelium.rnm_check", run_rnm_check)
    result = execute_workflow(
        recipe, registry, _run(tmp_path), tools={"xrun": str(xrun)}
    )

    assert result.status == "PASS"
    assert result.gates["model_check"].status == "PASS"
    assert result.gates["model_check"].summary["pass_marker_found"] is True
    assert result.gates["model_check"].summary["l1"]["case_coverage"] == 1.0
    assert result.gates["model_check"].summary["l1"]["findings"] == []


def test_xcelium_l1_evidence_fails_closed_on_missing_case(tmp_path: Path) -> None:
    recipe = load_builtin_recipe("saradc.comparator_new")
    xrun = tmp_path / "xrun"
    xrun.write_text(
        "#!/bin/sh\n"
        "echo 'TOOL: xrun(64) 23.09-s002: Started'\n"
        "case \"$*\" in *smoke_tb.sv*)\n"
        "echo 'AIVW_L1_EVENT {\"event\":\"check\",\"id\":\"initial-state\",\"status\":\"PASS\"}'\n"
        "echo 'AIVW_L1_EVENT {\"event\":\"check\",\"id\":\"clock-hold\",\"status\":\"PASS\"}'\n"
        "echo 'AIVW_RNM_SMOKE checks=5 PASS'\n"
        ";; esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    xrun.chmod(0o755)
    registry = builtin_registry(executor_handlers={})

    def pass_handler(_context: ExecutorContext) -> ExecutorResult:
        return ExecutorResult("PASS")

    def generate(context: ExecutorContext) -> ExecutorResult:
        contract_path = context.recipe.input_path("interface_contract")
        spec_path = context.recipe.input_path("behavior_spec")
        plugin = registry.require_model_class(context.recipe.model_class).handler
        assert plugin is not None
        candidate = plugin(
            BaselineGenerationRequest(
                contract=load_contract(contract_path),
                spec=json.loads(spec_path.read_text(encoding="utf-8")),
                contract_sha256="contract",
                spec_sha256="spec",
                generated_at="test",
                verification=context.recipe.payload["verification"],
            )
        )
        model = context.run.payload_root / "models" / "candidate.rnm.sv"
        tb = context.run.payload_root / "xcelium" / "candidate_smoke_tb.sv"
        model.parent.mkdir()
        tb.parent.mkdir()
        model.write_text(candidate.source, encoding="utf-8")
        tb.write_text(candidate.smoke_testbench, encoding="utf-8")
        return ExecutorResult(
            "PASS",
            outputs={
                "model_source": model.relative_to(context.run.payload_root).as_posix(),
                "smoke_testbench": tb.relative_to(context.run.payload_root).as_posix(),
                "pass_marker": "AIVW_RNM_SMOKE checks=5 PASS",
                "l1_test_plan": candidate.test_plan,
            },
            artifacts=(model, tb),
        )

    for name in ("virtuoso.snapshot", "cadence.si", "connectivity.check"):
        registry.bind_executor(name, pass_handler)
    registry.bind_executor("ai.generate_model", generate)
    registry.bind_executor("xcelium.rnm_check", run_rnm_check)
    result = execute_workflow(
        recipe,
        registry,
        _run(tmp_path),
        tools={"xrun": str(xrun)},
        targets=("model_check",),
    )

    gate = result.gates["model_check"]
    assert gate.status == "FAIL_L1_EVIDENCE"
    assert {item["code"] for item in gate.summary["l1"]["findings"]} >= {
        "missing_case",
        "summary_count_mismatch",
    }


def test_xcelium_runner_creates_xmlib_parent_directories(tmp_path: Path) -> None:
    recipe = load_builtin_recipe("saradc.comparator_new")
    xrun = tmp_path / "xrun"
    xrun.write_text(
        "#!/bin/sh\n"
        "echo 'TOOL: xrun(64) 23.09-s002: Started'\n"
        "case \"$*\" in *-compile*) exit 0 ;; esac\n"
        "echo 'AIVW_L1_EVENT {\"event\":\"check\",\"id\":\"initial-state\",\"status\":\"PASS\"}'\n"
        "echo 'AIVW_L1_EVENT {\"event\":\"check\",\"id\":\"clock-hold\",\"status\":\"PASS\"}'\n"
        "echo 'AIVW_L1_EVENT {\"event\":\"case\",\"id\":\"nominal-positive\",\"type\":\"positive_differential\",\"region\":\"deterministic\",\"status\":\"PASS\",\"passed_assertions\":[\"decision\",\"out\",\"outb\",\"complementary\"],\"metrics\":{\"decision\":\"positive\",\"out\":1.2,\"outb\":0.0}}'\n"
        "echo 'AIVW_L1_EVENT {\"event\":\"case\",\"id\":\"nominal-negative\",\"type\":\"negative_differential\",\"region\":\"deterministic\",\"status\":\"PASS\",\"passed_assertions\":[\"decision\",\"out\",\"outb\",\"complementary\"],\"metrics\":{\"decision\":\"negative\",\"out\":0.0,\"outb\":1.2}}'\n"
        "echo 'AIVW_L1_EVENT {\"event\":\"case\",\"id\":\"nominal-zero\",\"type\":\"zero_differential\",\"region\":\"indeterminate\",\"status\":\"PASS\",\"passed_assertions\":[\"decision\",\"out\",\"outb\",\"complementary\"],\"metrics\":{\"decision\":\"tie\",\"out\":0.6,\"outb\":0.6}}'\n"
        "echo 'AIVW_L1_EVENT {\"event\":\"summary\",\"status\":\"PASS\",\"case_count\":3,\"check_count\":2,\"assertion_count\":12}'\n"
        "echo 'AIVW_RNM_SMOKE checks=5 PASS'\n"
        "exit 0\n",
        encoding="utf-8",
    )
    xrun.chmod(0o755)
    run = _run(tmp_path)
    contract_path = recipe.input_path("interface_contract")
    spec_path = recipe.input_path("behavior_spec")
    plugin = builtin_registry().require_model_class(recipe.model_class).handler
    assert plugin is not None
    candidate = plugin(
        BaselineGenerationRequest(
            contract=load_contract(contract_path),
            spec=json.loads(spec_path.read_text(encoding="utf-8")),
            contract_sha256="contract",
            spec_sha256="spec",
            generated_at="test",
            verification=recipe.payload["verification"],
        )
    )
    model = run.payload_root / "model.sv"
    testbench = run.payload_root / "tb.sv"
    model.write_text(candidate.source, encoding="utf-8")
    testbench.write_text(candidate.smoke_testbench, encoding="utf-8")
    context = ExecutorContext(
            recipe=recipe,
            run=run,
            gate_id="model_check",
            executor_name="xcelium.rnm_check",
            dependencies={
                "generate": ExecutorResult(
                    "PASS",
                    outputs={
                        "model_source": "model.sv",
                        "smoke_testbench": "tb.sv",
                        "pass_marker": "AIVW_RNM_SMOKE checks=5 PASS",
                        "l1_test_plan": candidate.test_plan,
                    },
                )
            },
            environment={},
            tools={"xrun": str(xrun)},
            timeout=30,
        )
    context.gate_root.mkdir(parents=True)
    result = run_rnm_check(context)
    assert result.status == "PASS"
    assert result.summary["checker"]["command"][0] == sys.executable
    assert (run.payload_root / "checks/gates/model_check/compile/xcelium.d").is_dir()
    assert (run.payload_root / "checks/gates/model_check/smoke/xcelium.d").is_dir()
