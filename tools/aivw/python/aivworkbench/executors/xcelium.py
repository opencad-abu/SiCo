"""Generic Xcelium RNM conformance, compile, and smoke executor."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Mapping

from ..executor import ExecutorContext, ExecutorResult, ExternalArtifactLocator
from ..l1 import build_l1_test_plan
from ..profiles import product_root
from ..plugins.xcelium_evidence import get_xcelium_evidence_adapter
from ..workspace import sha256_file, stable_digest, write_json_once
from ..agent.runtime_info import production_python
from .xcelium_binding import _compare_evidence_binding, _evidence_locators, _validate_evidence_binding
from .xcelium_events import _evaluate_l1_events
from .xcelium_process import _generation_outputs, _payload_file, _run, _xrun_version

def run_rnm_check(context: ExecutorContext) -> ExecutorResult:
    generated = _generation_outputs(context.dependencies)
    l1_plan = generated.get("l1_test_plan")
    try:
        canonical_l1_plan = build_l1_test_plan(context.recipe.payload["verification"])
    except (KeyError, TypeError, ValueError) as exc:
        return ExecutorResult(
            "BLOCKED_INPUT",
            {"reason": "recipe L1 test plan is invalid", "detail": str(exc)},
        )
    if not isinstance(l1_plan, Mapping) or stable_digest(l1_plan) != stable_digest(
        canonical_l1_plan
    ):
        return ExecutorResult(
            "BLOCKED_INPUT",
            {"reason": "generation L1 test plan does not match the recipe"},
        )
    # ``physical_pvt`` is a separate Spectre/AMS execution contract.  The
    # current executor is Xcelium-only and has no registered physical-corner
    # adapter, so never let it silently treat a physical request as an RNM
    # smoke run.  Keep this guard before any adapter validation, path
    # resolution, file creation, or subprocess invocation.
    if canonical_l1_plan.get("physical_pvt") is not None:
        return ExecutorResult(
            "BLOCKED_ENVIRONMENT",
            {
                "status": "BLOCKED_ENVIRONMENT",
                "code": "physical_pvt_adapter_unavailable",
                "physical_pvt_execution": "not_invoked",
                "design_verdict": "NOT_ESTABLISHED",
            },
        )
    adapter_name = generated.get("xcelium_evidence_adapter")
    evidence_adapter = None
    if adapter_name is not None:
        model_options = context.recipe.payload.get("model", {})
        recipe_adapter = (
            model_options.get("xcelium_evidence_adapter")
            if isinstance(model_options, Mapping)
            else None
        )
        if adapter_name != recipe_adapter:
            return ExecutorResult(
                "BLOCKED_INPUT",
                {
                    "reason": "generation Xcelium evidence adapter does not match recipe",
                    "expected": recipe_adapter,
                    "actual": adapter_name,
                },
            )
        evidence_adapter = get_xcelium_evidence_adapter(adapter_name)
        if evidence_adapter is None:
            return ExecutorResult(
                "BLOCKED_INPUT",
                {
                    "reason": "generation Xcelium evidence adapter is unknown",
                    "adapter": adapter_name,
                },
            )
        if not isinstance(l1_plan, Mapping):
            return ExecutorResult(
                "BLOCKED_INPUT",
                {"reason": "evidence adapter requires a canonical L1 plan"},
            )
        # Environment qualification is evaluated before contract validation so
        # an unavailable required tool is reported distinctly from an
        # unsupported backend/format.
        preflight = evidence_adapter.preflight_tools(context.tools, l1_plan)
        if preflight is not None:
            return ExecutorResult(preflight["status"], dict(preflight))
        stale_output_findings = evidence_adapter.preflight_outputs(
            context.run.payload_root, l1_plan
        )
        if stale_output_findings:
            return ExecutorResult(
                "STALE_ARTIFACT",
                {
                    "code": "xcelium_evidence_output_stale",
                    "reason": "adapter-owned evidence output exists before Xcelium invocation",
                    "findings": stale_output_findings,
                },
            )
    elif context.recipe.payload.get("model", {}).get("xcelium_evidence_adapter"):
        return ExecutorResult(
            "BLOCKED_INPUT",
            {
                "reason": "recipe selected an Xcelium evidence adapter but generation did not return it",
                "expected": context.recipe.payload["model"]["xcelium_evidence_adapter"],
                "actual": None,
            },
        )
    evidence_binding, binding_findings = _validate_evidence_binding(
        l1_plan, generated.get("xcelium_evidence_binding"), context.run.payload_root
    )
    if binding_findings:
        return ExecutorResult(
            "BLOCKED_INPUT",
            {
                "reason": "generation Xcelium evidence binding is invalid",
                "findings": binding_findings,
            },
        )
    if evidence_adapter is not None:
        adapter_findings = evidence_adapter.validate_plan(l1_plan, evidence_binding)
        if adapter_findings:
            return ExecutorResult(
                "BLOCKED_INPUT",
                {
                    "reason": "generation Xcelium evidence adapter is not qualified",
                    "findings": adapter_findings,
                },
            )
    model = _payload_file(context, generated, "model_source")
    testbench = _payload_file(context, generated, "smoke_testbench")
    marker = generated.get("pass_marker")
    if not isinstance(marker, str) or not marker:
        return ExecutorResult("BLOCKED_INPUT", {"reason": "generation output has no pass_marker"})
    xrun = context.tools.get("xrun", "")
    if not xrun or not Path(xrun).is_file():
        return ExecutorResult("BLOCKED_ENVIRONMENT", {"reason": "qualified xrun is unavailable"})
    root = context.gate_root
    checker_path = (
        product_root().parent
        / "ai"
        / "skills"
        / "schematic-to-rnm"
        / "scripts"
        / "check_rnm.py"
    )
    checker_log = root / "rnm-check.log"
    checker_json = root / "rnm-check.json"
    checker_command = (
        production_python(require_environment=True),
        str(checker_path),
        str(model),
        "--spec",
        str(context.recipe.input_path("behavior_spec")),
        "--format",
        "json",
        "--fail-on-warnings",
    )
    checker = _run(checker_command, root, context, checker_log)
    try:
        checker_payload: Any = json.loads(checker_log.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        checker_payload = {"raw_output": checker_log.read_text(encoding="utf-8", errors="replace")}
    write_json_once(
        checker_json,
        {**checker, "result": checker_payload},
    )
    if checker["returncode"] != 0 or checker["timed_out"]:
        return ExecutorResult(
            "FAIL_RNM_CHECKER" if not checker["timed_out"] else "BLOCKED_TIMEOUT",
            {"checker": checker},
            artifacts=(checker_log, checker_json),
        )
    compile_root = root / "compile"
    compile_root.mkdir()
    (compile_root / "xcelium.d").mkdir()
    smoke_root = root / "smoke"
    smoke_root.mkdir()
    (smoke_root / "xcelium.d").mkdir()
    preparation = None
    if evidence_adapter is not None:
        preparation = evidence_adapter.prepare_simulation(
            context.run.payload_root, smoke_root, l1_plan, evidence_binding
        )
    compile_log = compile_root / "xrun-compile.log"
    compile_args = preparation.compile_args if preparation is not None else ()
    compile_command = (
        xrun,
        "-compile",
        "-sv",
        "-nolog",
        "-xmlibdirpath",
        str(compile_root / "xcelium.d"),
        *compile_args,
        str(model),
    )
    compile_result = _run(compile_command, compile_root, context, compile_log)
    compile_text = compile_log.read_text(encoding="utf-8", errors="replace")
    compile_errors = [
        line.strip() for line in compile_text.splitlines() if re.search(r"\*[EF],", line)
    ]
    if compile_result["returncode"] != 0 or compile_result["timed_out"] or compile_errors:
        return ExecutorResult(
            "FAIL_COMPILE" if not compile_result["timed_out"] else "BLOCKED_TIMEOUT",
            {"checker": checker, "compile": compile_result, "compile_errors": compile_errors},
            artifacts=(checker_log, checker_json, compile_log),
        )
    smoke_log = smoke_root / "xrun-smoke.log"
    smoke_command = (
        xrun,
        "-sv",
        "-nolog",
        "-xmlibdirpath",
        str(smoke_root / "xcelium.d"),
        *(preparation.simulation_args if preparation is not None else ()),
        str(model),
        str(testbench),
    )
    # Evidence paths are payload-relative by contract.  Run the simulator from
    # the payload root so runtime-created SHM and coverage databases land at
    # the exact bound locations; keep the gate log in the smoke directory.
    smoke = _run(smoke_command, context.run.payload_root, context, smoke_log)
    smoke_text = smoke_log.read_text(encoding="utf-8", errors="replace")
    smoke_errors = [
        line.strip()
        for line in smoke_text.splitlines()
        if re.search(r"\*[EF],|\$fatal", line, re.IGNORECASE)
    ]
    marker_found = marker in smoke_text
    l1_evidence_path = root / "l1-evidence.json"
    l1_evidence: dict[str, Any] | None = None
    artifact_locators: list[ExternalArtifactLocator] = []
    l1_pass = True
    if isinstance(l1_plan, Mapping):
        l1_evidence = _evaluate_l1_events(l1_plan, smoke_text, context.run.payload_root)
        binding_findings = _compare_evidence_binding(
            evidence_binding, l1_evidence.get("events", [])
        )
        l1_evidence["findings"].extend(binding_findings)
        if binding_findings:
            l1_evidence["status"] = "FAIL"
        xrun_version = _xrun_version(compile_text, smoke_text)
        l1_evidence["binding"] = {
            "test_plan_sha256": stable_digest(l1_plan),
            "model": {
                "path": model.relative_to(context.run.payload_root).as_posix(),
                "sha256": sha256_file(model),
            },
            "testbench": {
                "path": testbench.relative_to(context.run.payload_root).as_posix(),
                "sha256": sha256_file(testbench),
            },
            "xrun": str(Path(xrun).absolute()),
            "xrun_version": xrun_version,
            "compile_command": list(compile_command),
            "simulation_command": list(smoke_command),
        }
        if smoke_errors:
            l1_evidence["findings"].append(
                {"code": "simulation_error_diagnostic", "diagnostics": smoke_errors}
            )
            l1_evidence["status"] = "FAIL"
        if xrun_version is None:
            l1_evidence["findings"].append(
                {"code": "xrun_identity_unavailable_or_ambiguous"}
            )
            l1_evidence["status"] = "FAIL"
        if evidence_adapter is not None and preparation is not None:
            l1_evidence["adapter"] = dict(preparation.metadata)
            l1_evidence["adapter"]["binding"] = dict(evidence_binding)
            adapter_findings = evidence_adapter.verify_outputs(
                context.run.payload_root, l1_plan, l1_evidence
            )
            if adapter_findings:
                l1_evidence["findings"].extend(adapter_findings)
                l1_evidence["status"] = "FAIL"
        write_json_once(l1_evidence_path, l1_evidence)
        l1_pass = l1_evidence["status"] == "PASS"
        artifact_locators = _evidence_locators(
            l1_evidence, context.run.payload_root, evidence_binding
        )
    status = (
        "PASS"
        if smoke["returncode"] == 0
        and not smoke["timed_out"]
        and marker_found
        and l1_pass
        else "BLOCKED_TIMEOUT"
        if smoke["timed_out"]
        else "FAIL_L1_EVIDENCE"
        if l1_evidence is not None and not l1_pass
        else "FAIL_RNM_TB"
    )
    artifacts = [checker_log, checker_json, compile_log, smoke_log]
    if preparation is not None:
        artifacts.extend(preparation.artifacts)
    if l1_evidence is not None:
        artifacts.append(l1_evidence_path)
    outputs = {
        "validated_model": generated["model_source"],
        "smoke_log": smoke_log.relative_to(context.run.payload_root).as_posix(),
    }
    if l1_evidence is not None:
        outputs["l1_evidence"] = l1_evidence_path.relative_to(
            context.run.payload_root
        ).as_posix()
        outputs["l1_case_coverage"] = l1_evidence["case_coverage"]
    return ExecutorResult(
        status,
        {
            "checker": checker,
            "compile": compile_result,
            "compile_errors": compile_errors,
            "smoke": smoke,
            "smoke_errors": smoke_errors,
            "pass_marker": marker,
            "pass_marker_found": marker_found,
            "l1": l1_evidence,
        },
        outputs,
        tuple(artifacts),
        tuple(artifact_locators),
    )

__all__ = ["run_rnm_check"]
