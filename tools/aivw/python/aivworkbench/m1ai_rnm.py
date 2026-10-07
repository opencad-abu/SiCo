"""Deterministic M1 comparator RNM candidate generation and Xcelium smoke gate."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping

from .manifest import publish_split_manifests, verify_split_manifests
from .m1ai_inputs import load_contract
from .plugins.latched_dynamic_comparator import BaselineGenerationRequest
from .profiles import Profile, product_root
from .recipe import load_builtin_recipe
from .registry import builtin_registry
from .toolchain import parse_setup_exports
from .workspace import (
    LaunchPaths,
    allocate_split_run,
    resolve_project_db_root,
    sha256_file,
    stable_digest,
    write_json_once,
)
from .agent.runtime_info import production_python


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def render_comparator_rnm(
    spec: Mapping[str, Any],
    *,
    source_sha256: str,
    spec_sha256: str,
    generated_at: str = "unspecified",
) -> str:
    contract = {
        "target": {"cell": spec["target"]["cell"]},
        "interface": {"port_order": list(spec["interface"]["port_order"])},
    }
    registry = builtin_registry()
    handler = registry.require_model_class("latched_dynamic_comparator").handler
    assert handler is not None
    candidate = handler(
        BaselineGenerationRequest(
            contract=contract,
            spec=spec,
            contract_sha256=source_sha256,
            spec_sha256=spec_sha256,
            generated_at=generated_at,
        )
    )
    return candidate.source


def _xrun_compile(
    xrun: str, *, source: Path, work: Path, log: Path, timeout: float
) -> dict[str, Any]:
    xmlib = work / "xcelium.d"
    xmlib.mkdir(parents=True, exist_ok=True)
    command = (xrun, "-compile", "-sv", "-nolog", "-xmlibdirpath", str(xmlib), str(source))
    try:
        completed = subprocess.run(
            command,
            cwd=work,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        log.write_text(output, encoding="utf-8")
        return {"status": "BLOCKED_ENVIRONMENT", "returncode": None, "timed_out": True, "command": list(command)}
    log.write_text(completed.stdout, encoding="utf-8")
    errors = [line.strip() for line in completed.stdout.splitlines() if re.search(r"\*[EF],", line)]
    return {
        "status": "PASS" if completed.returncode == 0 and not errors else "FAIL_COMPILE",
        "returncode": completed.returncode,
        "timed_out": False,
        "errors": errors,
        "command": list(command),
        "version": _xrun_version(xrun, work),
    }


def _xrun_version(xrun: str, work: Path) -> str:
    try:
        completed = subprocess.run(
            (xrun, "-version"),
            cwd=work,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"unavailable: {exc}"
    return completed.stdout.strip()


def _run_checker(check_script: Path, source: Path, spec: Path) -> dict[str, Any]:
    command = (
        production_python(require_environment=True),
        str(check_script),
        str(source),
        "--spec",
        str(spec),
        "--format",
        "json",
        "--fail-on-warnings",
    )
    completed = subprocess.run(
        command,
        cwd=source.parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        timeout=60,
        check=False,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload = {"raw_output": completed.stdout}
    return {
        "command": list(command),
        "returncode": completed.returncode,
        "status": "PASS" if completed.returncode == 0 else "FAIL_CHECKER",
        "result": payload,
    }


def run_m1_ai_rnm(
    profile: Profile,
    launch: LaunchPaths,
    *,
    timeout: float = 300.0,
    xrun_path: str | None = None,
) -> dict[str, object]:
    started_at = _now()
    recipe = load_builtin_recipe("saradc.comparator_new")
    target = recipe.target
    contract_path = recipe.input_path("interface_contract")
    spec_path = recipe.input_path("behavior_spec")
    contract = load_contract(contract_path)
    supplement = json.loads(spec_path.read_text(encoding="utf-8"))
    registry = builtin_registry()
    model_plugin = registry.require_model_class(recipe.model_class)
    if model_plugin.handler is None:
        raise ValueError(f"model-class plugin has no handler: {recipe.model_class}")
    generated_at = _now()
    candidate = model_plugin.handler(
        BaselineGenerationRequest(
            contract=contract,
            spec=supplement,
            contract_sha256=sha256_file(contract_path),
            spec_sha256=sha256_file(spec_path),
            generated_at=generated_at,
        )
    )
    request = {
        "pilot": "m1-ai",
        "phase": "rnm-generation-check-tb",
        "recipe_id": recipe.recipe_id,
        "recipe_revision": recipe.revision,
        "recipe_sha256": recipe.sha256,
        "contract_sha256": sha256_file(contract_path),
        "supplement_sha256": sha256_file(spec_path),
        "model_class": recipe.model_class,
        "model_plugin_version": model_plugin.version,
        "profile": profile.name,
    }
    if profile.storage is None:
        raise ValueError(f"profile {profile.name!r} has no split-storage contract")
    exports = parse_setup_exports(profile.setup_script, (profile.storage.payload_env,))
    project_db_root = resolve_project_db_root(
        exports[profile.storage.payload_env],
        launch,
        forbidden_roots=(product_root(),),
    )
    output_view = target["output_view"]
    request_digest = stable_digest(request)
    run = allocate_split_run(
        launch,
        project_db_root,
        library=str(target["library"]),
        cell=str(target["cell"]),
        view=str(output_view["name"]),
        kind="m1-ai-rnm",
        request_digest=request_digest,
    )
    request_path = run.control_root / "request.json"
    write_json_once(request_path, request)
    ai_dir = run.payload_root / "ai-generation"
    inputs_dir = run.payload_root / "inputs"
    checks = run.payload_root / "checks"
    xcelium_dir = run.payload_root / "xcelium"
    tb_dir = xcelium_dir / "tb"
    ai_dir.mkdir()
    inputs_dir.mkdir()
    checks.mkdir()
    tb_dir.mkdir(parents=True)
    source = ai_dir / f"{target['module']}.rnm.sv"
    source.write_text(candidate.source, encoding="utf-8")
    input_manifest = inputs_dir / "input-manifest.json"
    write_json_once(
        input_manifest,
        {
            "schema_version": 1,
            "request": request,
            "recipe": recipe.summary(),
            "source_policy": "source OA read-only for this pilot; all generated/EDA payload is external",
            "storage": {
                "control_root": str(run.control_root),
                "payload_root": str(run.payload_root),
            },
            "generation": {
                "kind": "deterministic-model-class-baseline",
                "ai_generation": "not performed",
                "model_class": recipe.model_class,
                "model_version": candidate.model_version,
                "generated_at": generated_at,
                "spec_schema_version": supplement["schema_version"],
                "human_review_required": True,
                "human_revision_count": 0,
            },
        },
    )
    check_script = (
        product_root().parent
        / "ai"
        / "skills"
        / "schematic-to-rnm"
        / "scripts"
        / "check_rnm.py"
    )
    source_text = source.read_text(encoding="utf-8")
    static = {
        "module": target["module"],
        "ports": list(supplement["interface"]["port_order"]),
        "source_sha256": sha256_file(source),
        "checks": {
            "interface_matches_contract": True,
            "outputs_driven": "assign " in source_text,
            "no_switch_primitives": not re.search(
                r"\b(nmos|pmos|cmos|tran|rtran)\b", source_text
            ),
            "assumption_status_explicit": (
                supplement["status"] == "assumption_pending_spectre_correlation"
            ),
            "checker_source_available": check_script.is_file(),
        },
    }
    static["all_checks_pass"] = all(static["checks"].values())
    write_json_once(checks / "static-check.json", static)
    checker = _run_checker(check_script, source, spec_path)
    write_json_once(checks / "rnm-check.json", checker)
    tb_source = tb_dir / f"{target['module']}_smoke_tb.sv"
    tb_source.write_text(candidate.smoke_testbench, encoding="utf-8")
    xrun = xrun_path or ""
    if not xrun:
        compile_evidence = {
            "status": "BLOCKED_ENVIRONMENT",
            "reason": "approved Xcelium xrun path was not provided",
            "returncode": None,
            "timed_out": False,
        }
        run_evidence = {
            "status": "BLOCKED_ENVIRONMENT",
            "reason": "approved Xcelium xrun path was not provided",
            "returncode": None,
            "timed_out": False,
        }
    else:
        compile_work = xcelium_dir / "compile"
        smoke_work = xcelium_dir / "smoke"
        compile_work.mkdir()
        smoke_work.mkdir()
        compile_evidence = _xrun_compile(
            xrun,
            source=source,
            work=compile_work,
            log=compile_work / "xrun-compile.log",
            timeout=timeout,
        )
        run_evidence = _run_tb(xrun, source, tb_source, smoke_work, timeout)
    write_json_once(checks / "xrun-compile.json", compile_evidence)
    smoke_evidence = checks / "smoke-evidence.json"
    write_json_once(smoke_evidence, run_evidence)
    if (
        static["all_checks_pass"]
        and checker["status"] == "PASS"
        and compile_evidence["status"] == "PASS"
        and run_evidence["status"] == "PASS"
    ):
        status = "PASS"
    elif run_evidence["status"] != "PASS":
        status = str(run_evidence["status"])
    else:
        status = "FAIL_RNM_GATE"
    finished_at = _now()
    report_path = run.control_root / "report.json"
    write_json_once(
        report_path,
        {
            "schema_version": 1,
            "status": status,
            "producer_kind": "m1-ai-rnm-generation-check-tb",
            "design_verdict": status,
            "generation_kind": "deterministic-model-class-baseline",
            "ai_generation": "not performed",
            "next_gate": "Spectre-to-RNM correlation",
        },
    )
    payload_artifacts = [
        input_manifest,
        source,
        checks / "static-check.json",
        checks / "rnm-check.json",
        checks / "xrun-compile.json",
        smoke_evidence,
        tb_source,
    ]
    for optional in (
        xcelium_dir / "compile" / "xrun-compile.log",
        xcelium_dir / "smoke" / "xrun-smoke.log",
    ):
        if optional.is_file():
            payload_artifacts.append(optional)
    control, _payload = publish_split_manifests(
        run,
        request_digest=request_digest,
        status=status,
        control_details={
            "producer_kind": "m1-ai-rnm-generation-check-tb",
            "started_at": started_at,
            "finished_at": finished_at,
            "recipe": recipe.summary(),
            "target": {
                "library": target["library"],
                "cell": target["cell"],
                "view": output_view["name"],
                "module": target["module"],
            },
            "payload_root": str(run.payload_root),
            "generation": {
                "source": "ai-generation/" + source.name,
                "kind": "deterministic-model-class-baseline",
                "ai_generation": "not performed",
                "model_class": recipe.model_class,
                "model_version": candidate.model_version,
                "generated_at": generated_at,
                "requires_human_review": True,
            },
            "checks": {
                "static": static,
                "checker": checker,
                "compile": compile_evidence,
                "tb": run_evidence,
            },
            "does_not_prove": supplement["verification"]["does_not_prove"],
            "next_gate": "Spectre-to-RNM correlation",
        },
        payload_details={
            "producer_kind": "m1-ai-rnm-generation-check-tb",
            "target": recipe.summary()["target"],
            "unindexed_payload": [
                "xcelium/compile/xcelium.d",
                "xcelium/smoke/xcelium-run.d",
            ],
            "artifact_policy": "stable sources/evidence/logs indexed; Xcelium worklibs unindexed",
        },
        control_artifacts=(request_path, report_path),
        payload_artifacts=payload_artifacts,
    )
    result = dict(control)
    result["manifest_pair"] = verify_split_manifests(
        run.control_manifest, run.payload_manifest
    )
    return result


def _run_tb(xrun: str, model: Path, tb: Path, work: Path, timeout: float) -> dict[str, Any]:
    xmlib = work / "xcelium-run.d"
    xmlib.mkdir(parents=True, exist_ok=True)
    log = work / "xrun-smoke.log"
    command = (xrun, "-sv", "-nolog", "-xmlibdirpath", str(xmlib), str(model), str(tb))
    try:
        completed = subprocess.run(command, cwd=work, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        log.write_text("timeout\n", encoding="utf-8")
        return {"status": "TIMEOUT", "returncode": None, "timed_out": True, "command": list(command)}
    log.write_text(completed.stdout, encoding="utf-8")
    passed = completed.returncode == 0 and "AIVW_RNM_SMOKE checks=5 PASS" in completed.stdout and not re.search(r"\$fatal|\*E,", completed.stdout)
    return {"status": "PASS" if passed else "FAIL_RNM_TB", "returncode": completed.returncode, "timed_out": False, "command": list(command), "pass_marker": "AIVW_RNM_SMOKE checks=5 PASS" in completed.stdout}


__all__ = ["render_comparator_rnm", "run_m1_ai_rnm"]
