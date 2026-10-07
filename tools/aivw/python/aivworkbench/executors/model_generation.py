"""Generic recipe-driven model candidate generation executor.

The registered model-class plugin owns behavior synthesis.  This adapter only
loads the recipe inputs, records provenance, and materializes bounded payload
files.  Structural connectivity is carried separately: a plugin may provide a
``structure_source`` on its candidate, otherwise this gate refuses to claim a
connectivity input.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from ..executor import ExecutorContext, ExecutorResult
from ..l1 import build_l1_test_plan
from ..m1ai_inputs import load_contract
from ..model import ModelGenerationRequest
from ..workspace import sha256_file, stable_digest, write_json_once
from ..connectivity import parse_globalmap


def run_model_generation(context: ExecutorContext) -> ExecutorResult:
    structure = context.dependencies.get("structure")
    if structure is None or structure.status != "PASS":
        return ExecutorResult("BLOCKED_INPUT", {"code": "structure_dependency"})
    if structure.outputs.get("structure_authoritative") is not True:
        return ExecutorResult("BLOCKED_INPUT", {"code": "structure_not_authoritative"})
    try:
        contract_path = context.recipe.input_path("interface_contract")
        spec_path = context.recipe.input_path("behavior_spec")
        contract = load_contract(contract_path)
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        if not isinstance(spec, dict):
            raise ValueError("behavior spec root is not an object")
        registry = context.metadata.get("registry")
        if registry is None:
            raise ValueError("generation registry is unavailable")
        plugin = registry.require_model_class(context.recipe.model_class)
        if plugin.handler is None:
            raise ValueError(f"model class has no handler: {context.recipe.model_class}")
        canonical_test_plan = build_l1_test_plan(
            context.recipe.payload["verification"]
        )
        request = ModelGenerationRequest(
            contract=contract,
            spec=spec,
            contract_sha256=sha256_file(contract_path),
            spec_sha256=sha256_file(spec_path),
            generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            verification=deepcopy(context.recipe.payload["verification"]),
            model_options=deepcopy(context.recipe.payload.get("model", {})),
        )
        candidate = plugin.handler(request)
        candidate_test_plan = getattr(candidate, "test_plan", None)
        if not isinstance(candidate_test_plan, dict) or stable_digest(
            candidate_test_plan
        ) != stable_digest(canonical_test_plan):
            raise ValueError("model-class candidate changed the recipe-owned L1 test plan")
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        return ExecutorResult("BLOCKED_INPUT", {"code": "generation_input_invalid", "detail": str(exc)})
    root = context.run.payload_root / "models"
    root.mkdir(parents=True, exist_ok=True)
    model = root / f"{context.recipe.target['module']}.rnm.sv"
    smoke = root / f"{context.recipe.target['module']}.smoke_tb.sv"
    provenance = root / "generation-evidence.json"
    model.write_text(candidate.source, encoding="utf-8")
    smoke.write_text(candidate.smoke_testbench, encoding="utf-8")
    structure_source = getattr(candidate, "structure_source", None)
    test_plan = canonical_test_plan
    check_count = (
        len(test_plan.get("required_checks", [])) + len(test_plan.get("cases", []))
        if isinstance(test_plan, dict)
        else 5
    )
    outputs: dict[str, object] = {
        "model_source": str(model.relative_to(context.run.payload_root)),
        "smoke_testbench": str(smoke.relative_to(context.run.payload_root)),
        "pass_marker": f"AIVW_RNM_SMOKE checks={check_count} PASS",
        "model_version": str(getattr(candidate, "model_version", "unspecified")),
    }
    if isinstance(test_plan, dict):
        outputs["l1_test_plan"] = test_plan
    evidence_binding = getattr(candidate, "xcelium_evidence_binding", None)
    if evidence_binding is not None:
        if not isinstance(evidence_binding, dict):
            return ExecutorResult(
                "BLOCKED_INPUT",
                {"code": "generation_evidence_binding_invalid"},
            )
        outputs["xcelium_evidence_binding"] = deepcopy(evidence_binding)
    evidence_adapter = getattr(candidate, "xcelium_evidence_adapter", None)
    if evidence_adapter is not None:
        if not isinstance(evidence_adapter, str) or not evidence_adapter:
            return ExecutorResult(
                "BLOCKED_INPUT",
                {"code": "generation_evidence_adapter_invalid"},
            )
        outputs["xcelium_evidence_adapter"] = evidence_adapter
    artifacts = [model, smoke, provenance]
    if isinstance(structure_source, str) and structure_source:
        structural = root / f"{context.recipe.target['module']}.structure.sv"
        structural.write_text(structure_source, encoding="utf-8")
        outputs["connectivity_source"] = str(structural.relative_to(context.run.payload_root))
        artifacts.append(structural)
    else:
        # The baseline plugin has no behavioral knowledge of hierarchy.  For
        # the M2 qualification path we materialize the exact authoritative SI
        # top-level module as a structural candidate.  This is a copy of
        # Cadence output, not AI-invented topology; future AI backends may
        # provide an independently assembled structure candidate instead.
        try:
            refs = structure.outputs.get("netlist")
            expected_module = str(context.recipe.target.get("module", ""))
            if isinstance(refs, list):
                structural_parts: list[str] = []
                for ref in refs:
                    if not isinstance(ref, str):
                        continue
                    source = _payload_file(context, ref)
                    text = source.read_text(encoding="utf-8", errors="replace")
                    structural_parts.append(text)
                if structural_parts and any(
                    re.search(rf"\bmodule\s+{re.escape(expected_module)}\b", part)
                    for part in structural_parts
                ):
                    structural = root / f"{expected_module}.structure.sv"
                    structural.write_text("\n\n".join(structural_parts), encoding="utf-8")
                    outputs["connectivity_source"] = str(structural.relative_to(context.run.payload_root))
                    artifacts.append(structural)
            globalmap_ref = structure.outputs.get("globalmap")
            if isinstance(globalmap_ref, str):
                globalmap = parse_globalmap(_payload_file(context, globalmap_ref).read_text(encoding="utf-8", errors="replace"))
                outputs["global_mapping"] = [list(item) for item in globalmap]
        except (OSError, ValueError) as exc:
            return ExecutorResult("BLOCKED_INPUT", {"code": "structure_artifact_invalid", "detail": str(exc)})
    # Materialize all official per-module maps into a small provenance record;
    # the connectivity gate reads them from the SI payload, while this output
    # makes the alias source explicit to downstream consumers.
    evidence = {
        "schema_version": 1,
        "status": "PASS",
        "model": context.recipe.model_class,
        "model_class": context.recipe.model_class,
        "model_version": str(getattr(candidate, "model_version", "unspecified")),
        "model_plugin_version": plugin.version,
        "generation_mode": "registered_model_class_candidate",
        "prompt_hash": None,
        "prompt_used": False,
        "template_hashes": [],
        "context_hashes": {
            "contract": sha256_file(contract_path),
            "behavior_spec": sha256_file(spec_path),
            "verification": stable_digest(context.recipe.payload["verification"]),
            "source_generation": structure.outputs.get("source_generation"),
        },
        "revision_parent": None,
        "feedback_hashes": [],
        "contract_sha256": sha256_file(contract_path),
        "spec_sha256": sha256_file(spec_path),
        "model_sha256": sha256_file(model),
        "connectivity_source": outputs.get("connectivity_source"),
        "ai_generation": "not_performed_for_registered_baseline",
        "human_review_required": True,
    }
    required_provenance = set(context.recipe.payload["model"]["provenance_required"])
    missing_provenance = required_provenance - set(evidence)
    if missing_provenance:
        return ExecutorResult(
            "BLOCKED_INPUT",
            {
                "code": "generation_provenance_incomplete",
                "missing": sorted(missing_provenance),
            },
        )
    write_json_once(provenance, evidence)
    return ExecutorResult(
        "PASS",
        {"code": "candidate_generated", "evidence": evidence},
        outputs,
        tuple(artifacts),
    )


__all__ = ["run_model_generation"]


def _payload_file(context: ExecutorContext, value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe structure artifact path: {value}")
    candidate = context.run.payload_root / relative
    resolved = candidate.resolve()
    if candidate.is_symlink() or not resolved.is_file() or not resolved.is_relative_to(context.run.payload_root.resolve()):
        raise ValueError(f"structure artifact unavailable: {value}")
    return resolved
