"""Fail-closed loader for versioned, target-independent workflow recipes."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .errors import RecipeError
from .profiles import product_root
from .recipe_dag import validate_workflow
from .registry import PluginRegistry, builtin_registry
from .workspace import sha256_file
from .l1_matrix import normalize_l1_matrix
from .pvt_contract import normalize_physical_pvt_matrix


_ID = re.compile(r"^[a-z][a-z0-9_.-]*$")
_TOP_KEYS = {
    "schema_version",
    "recipe_id",
    "revision",
    "purpose",
    "target",
    "structure",
    "inputs",
    "model",
    "workflow",
    "verification",
    "live_update",
    "publication",
}
_METRIC_KINDS = {"categorical", "numeric", "allowed_set", "timing", "waveform", "statistical"}
_XCELIUM_EVIDENCE_ADAPTERS = {
    "latched_dynamic_comparator.xcelium_evidence.v1",
}
_MODEL_KEYS = {
    "class",
    "generation",
    "template_policy",
    "provenance_required",
    "xcelium_evidence_adapter",
}


@dataclass(frozen=True)
class Recipe:
    path: Path
    payload: Mapping[str, Any]
    sha256: str
    resolved_inputs: Mapping[str, Path]

    @property
    def recipe_id(self) -> str:
        return str(self.payload["recipe_id"])

    @property
    def revision(self) -> int:
        return int(self.payload["revision"])

    @property
    def target(self) -> Mapping[str, Any]:
        return self.payload["target"]

    @property
    def model_class(self) -> str:
        return str(self.payload["model"]["class"])

    def input_path(self, name: str) -> Path:
        try:
            return self.resolved_inputs[name]
        except KeyError as exc:
            raise RecipeError(f"recipe {self.recipe_id} has no input {name!r}") from exc

    def summary(self) -> dict[str, object]:
        output = self.target["output_view"]
        return {
            "status": "PASS",
            "recipe_id": self.recipe_id,
            "revision": self.revision,
            "path": str(self.path),
            "sha256": self.sha256,
            "target": {
                "library": self.target["library"],
                "cell": self.target["cell"],
                "module": self.target["module"],
                "view": output["name"],
                "language": output["language"],
            },
            "model_class": self.model_class,
        "gates": [item["id"] for item in self.payload["workflow"]["gates"]],
            "resolved_inputs": {name: str(path) for name, path in self.resolved_inputs.items()},
        }


def available_recipes() -> tuple[str, ...]:
    values = []
    for path in sorted((product_root() / "recipes").glob("**/*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, Mapping) and _ID.fullmatch(str(payload.get("recipe_id", ""))):
            values.append(str(payload["recipe_id"]))
    return tuple(values)


def load_builtin_recipe(
    recipe_id: str, *, registry: PluginRegistry | None = None
) -> Recipe:
    if not _ID.fullmatch(recipe_id):
        raise RecipeError(f"invalid recipe id: {recipe_id!r}")
    matches = []
    for path in (product_root() / "recipes").glob("**/*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, Mapping) and payload.get("recipe_id") == recipe_id:
            matches.append(path)
    if len(matches) != 1:
        raise RecipeError(f"recipe {recipe_id!r} resolved to {len(matches)} files")
    return load_recipe(matches[0], registry=registry)


def load_recipe(path: Path, *, registry: PluginRegistry | None = None) -> Recipe:
    source = path.expanduser().resolve()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecipeError(f"cannot read recipe {source}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise RecipeError("recipe root must be an object")
    _validate_top(payload)
    active_registry = registry or builtin_registry()
    _validate_target(payload["target"])
    _validate_structure(payload["structure"])
    resolved = _validate_inputs(payload["inputs"], source)
    _validate_model(payload["model"], active_registry)
    validate_workflow(payload["workflow"], active_registry)
    _validate_config_binding_contract(payload["target"], payload["workflow"])
    _validate_verification(payload["verification"])
    _validate_live_update(payload["live_update"])
    _validate_publication(payload["publication"], payload["target"])
    return Recipe(source, payload, sha256_file(source), resolved)


def _validate_top(payload: Mapping[str, Any]) -> None:
    unknown = set(payload) - _TOP_KEYS
    missing = _TOP_KEYS - set(payload)
    if unknown or missing:
        raise RecipeError(f"recipe top-level fields mismatch: missing={sorted(missing)} unknown={sorted(unknown)}")
    if payload.get("schema_version") != 1:
        raise RecipeError("unsupported recipe schema_version")
    recipe_id = _text(payload, "recipe_id", "recipe")
    if not _ID.fullmatch(recipe_id):
        raise RecipeError(f"invalid recipe id: {recipe_id!r}")
    revision = payload.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise RecipeError("recipe revision must be a positive integer")
    _text(payload, "purpose", "recipe")


def _validate_target(value: object) -> None:
    target = _object(value, "target")
    for key in ("library", "cell", "module"):
        _text(target, key, "target")
    views = _object(target.get("source_views"), "target.source_views")
    if not any(_optional_text(item) for item in views.values()):
        raise RecipeError("target.source_views must select at least one view")
    output = _object(target.get("output_view"), "target.output_view")
    for key in ("name", "view_type", "language", "primary_file"):
        _text(output, key, "target.output_view")
    language = output["language"]
    expected = {"systemverilog": "verilog.sv", "veriloga": "veriloga.va"}
    if language not in expected or output["primary_file"] != expected[language]:
        raise RecipeError("output language and primary_file are inconsistent")


def _validate_structure(value: object) -> None:
    structure = _object(value, "structure")
    if _text(structure, "provider", "structure") not in {"cadence_si", "hed_ade_unl", "runams"}:
        raise RecipeError("unsupported authoritative structure provider")
    if structure.get("authoritative") is not True:
        raise RecipeError("structure provider must be authoritative")
    _strings(structure.get("required_artifacts"), "structure.required_artifacts")


def _validate_config_binding_contract(target: object, workflow: object) -> None:
    """Require an explicit snapshot-bound config gate for AMS recipes."""
    target_obj = _object(target, "target")
    views = _object(target_obj.get("source_views"), "target.source_views")
    config_view = views.get("config")
    if config_view is None:
        return
    if not isinstance(config_view, str) or not config_view:
        raise RecipeError("target.source_views.config must be a non-empty string when selected")
    gates = _object(workflow, "workflow").get("gates")
    if not isinstance(gates, list):
        raise RecipeError("workflow.gates must be an array")
    matches = [
        gate
        for gate in gates
        if isinstance(gate, Mapping) and gate.get("executor") == "virtuoso.config_binding"
    ]
    if len(matches) != 1:
        raise RecipeError(
            "config-backed recipes require exactly one virtuoso.config_binding gate"
        )
    needs = matches[0].get("needs")
    if not isinstance(needs, list) or "snapshot" not in needs:
        raise RecipeError("virtuoso.config_binding must depend directly on snapshot")


def _validate_inputs(value: object, recipe_path: Path) -> dict[str, Path]:
    inputs = _object(value, "inputs")
    required = {"interface_contract", "behavior_spec", "correlation_policy"}
    optional = {"experiment_contract", "exploratory_policy", "characterization_plan", "acceptance_policy",
                "characterization_source", "identification_options", "dynamic_acceptance"}
    if not required.issubset(inputs) or set(inputs) - required - optional:
        raise RecipeError(
            f"recipe inputs require {sorted(required)}; optional inputs are {sorted(optional)}"
        )
    return {name: _resolve_reference(_text(inputs, name, "inputs"), recipe_path) for name in inputs}


def _resolve_reference(reference: str, recipe_path: Path) -> Path:
    prefixes = {"product:": product_root(), "recipe:": recipe_path.parent}
    selected = next((item for item in prefixes if reference.startswith(item)), None)
    if selected is None:
        raise RecipeError(f"input reference requires product: or recipe: scope: {reference}")
    relative = Path(reference[len(selected) :])
    if relative.is_absolute() or ".." in relative.parts:
        raise RecipeError(f"unsafe input reference: {reference}")
    base = prefixes[selected].resolve()
    resolved = (base / relative).resolve()
    if not resolved.is_relative_to(base) or not resolved.is_file():
        raise RecipeError(f"recipe input is unavailable: {reference}")
    return resolved


def _validate_model(value: object, registry: PluginRegistry) -> None:
    model = _object(value, "model")
    unknown = set(model) - _MODEL_KEYS
    if unknown:
        raise RecipeError(f"model fields are unsupported: {sorted(unknown)}")
    registry.require_model_class(_text(model, "class", "model"))
    if model.get("generation") not in {"ai_iterative", "registered_identification"}:
        raise RecipeError("model.generation must be ai_iterative or registered_identification")
    if model.get("generation") == "registered_identification" and "system-identification" not in registry.require_model_class(model["class"]).capabilities:
        raise RecipeError("registered identification requires a capable model class")
    if model.get("template_policy") not in {"reference_only", "registered_scaffold"}:
        raise RecipeError("model.template_policy is unsupported")
    required = set(_strings(model.get("provenance_required"), "model.provenance_required"))
    if not {"model", "prompt_hash", "context_hashes", "revision_parent"}.issubset(required):
        raise RecipeError("model provenance contract is incomplete")
    if "xcelium_evidence_adapter" in model:
        adapter = model["xcelium_evidence_adapter"]
        if (
            not isinstance(adapter, str)
            or not adapter
            or adapter != adapter.strip()
        ):
            raise RecipeError("model.xcelium_evidence_adapter must be non-empty text")
        if adapter not in _XCELIUM_EVIDENCE_ADAPTERS:
            raise RecipeError(f"unknown model.xcelium_evidence_adapter: {adapter}")


def _validate_verification(value: object) -> None:
    verification = _object(value, "verification")
    metrics = verification.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        raise RecipeError("verification.metrics must be a non-empty array")
    names = []
    for raw in metrics:
        metric = _object(raw, "verification.metric")
        names.append(_text(metric, "name", "verification.metric"))
        if metric.get("kind") not in _METRIC_KINDS:
            raise RecipeError(f"unsupported metric kind: {metric.get('kind')}")
    if len(names) != len(set(names)):
        raise RecipeError("verification metric names must be unique")
    cases = verification.get("cases")
    if not isinstance(cases, list) or not cases:
        raise RecipeError("verification.cases must be a non-empty array")
    case_ids: list[str] = []
    for raw in cases:
        case = _object(raw, "verification.case")
        case_id = _text(case, "id", "verification.case")
        case_ids.append(case_id)
        _text(case, "type", "verification.case")
        _object(case.get("inputs"), f"verification.case {case_id}.inputs")
        if "corner" in case:
            _object(case["corner"], f"verification.case {case_id}.corner")
        if "vector_id" in case:
            _text(case, "vector_id", f"verification.case {case_id}")
        expected = _object(
            case.get("expected"), f"verification.case {case_id}.expected"
        )
        unknown_metrics = set(expected) - set(names)
        if unknown_metrics:
            raise RecipeError(
                f"verification case {case_id} has unknown metrics: {sorted(unknown_metrics)}"
            )
        assertions = _strings(
            case.get("assertions"), f"verification.case {case_id}.assertions"
        )
        if len(assertions) != len(set(assertions)):
            raise RecipeError(f"verification case {case_id} has duplicate assertions")
    if len(case_ids) != len(set(case_ids)):
        raise RecipeError("verification case IDs must be unique")
    l1 = _object(verification.get("l1"), "verification.l1")
    if _text(l1, "protocol", "verification.l1") != "AIVW_L1_EVENT v1 JSONL":
        raise RecipeError("verification.l1 protocol is unsupported")
    for key in (
        "require_exact_case_set",
        "require_exact_metric_set",
        "require_assertion_pass",
    ):
        if l1.get(key) is not True:
            raise RecipeError(f"verification.l1.{key} must be true")
    coverage = l1.get("minimum_case_coverage")
    if (
        isinstance(coverage, bool)
        or not isinstance(coverage, (int, float))
        or not 0.0 <= float(coverage) <= 1.0
    ):
        raise RecipeError("verification.l1.minimum_case_coverage must be between 0 and 1")
    required_metrics = _strings(
        l1.get("required_metrics"), "verification.l1.required_metrics"
    )
    if len(required_metrics) != len(set(required_metrics)) or not set(
        required_metrics
    ).issubset(set(names)):
        raise RecipeError("verification.l1.required_metrics must be unique known metrics")
    required_checks = _strings(
        l1.get("required_checks"), "verification.l1.required_checks"
    )
    if len(required_checks) != len(set(required_checks)):
        raise RecipeError("verification.l1.required_checks must be unique")
    if _text(l1, "hold_case", "verification.l1") not in set(case_ids):
        raise RecipeError("verification.l1.hold_case must identify a declared case")
    temporal = l1.get("temporal_assertions", [])
    if not isinstance(temporal, list):
        raise RecipeError("verification.l1.temporal_assertions must be an array")
    temporal_ids: list[str] = []
    for raw in temporal:
        assertion = _object(raw, "verification.l1.temporal_assertion")
        identifier = _text(
            assertion, "id", "verification.l1.temporal_assertion"
        )
        temporal_ids.append(identifier)
        _text(assertion, "property", f"temporal assertion {identifier}")
        _text(assertion, "clock", f"temporal assertion {identifier}")
        if "severity" in assertion:
            _text(assertion, "severity", f"temporal assertion {identifier}")
    if len(temporal_ids) != len(set(temporal_ids)):
        raise RecipeError("temporal assertion IDs must be unique")
    if "coverage" in l1:
        coverage_contract = _object(l1["coverage"], "verification.l1.coverage")
        _text(coverage_contract, "id", "verification.l1.coverage")
        if coverage_contract.get("backend") not in {"ucis", "imc"}:
            raise RecipeError("verification.l1.coverage.backend must be ucis or imc")
        score = coverage_contract.get("minimum_score")
        if (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not 0.0 <= float(score) <= 1.0
        ):
            raise RecipeError("verification.l1.coverage.minimum_score must be between 0 and 1")
        points = _strings(
            coverage_contract.get("required_points"),
            "verification.l1.coverage.required_points",
            empty=True,
        )
        if len(points) != len(set(points)):
            raise RecipeError("verification.l1.coverage.required_points must be unique")
    if "matrix" in l1:
        try:
            normalize_l1_matrix(
                l1["matrix"],
                [item for item in cases if isinstance(item, Mapping)],
            )
        except (TypeError, ValueError) as exc:
            raise RecipeError(f"verification.l1.matrix is invalid: {exc}") from exc
    if "waveform" in l1:
        waveform = _object(l1["waveform"], "verification.l1.waveform")
        _text(waveform, "id", "verification.l1.waveform")
        if waveform.get("format") not in {"shm", "vcd", "fsdb"}:
            raise RecipeError("verification.l1.waveform.format is unsupported")
        if waveform.get("retention") not in {"always", "on_failure", "never"}:
            raise RecipeError("verification.l1.waveform.retention is unsupported")
    if "physical_pvt" in verification:
        try:
            normalize_physical_pvt_matrix(
                verification["physical_pvt"],
                [item for item in cases if isinstance(item, Mapping)],
            )
        except (TypeError, ValueError) as exc:
            raise RecipeError(f"verification.physical_pvt is invalid: {exc}") from exc


def _validate_live_update(value: object) -> None:
    live = _object(value, "live_update")
    triggers = set(_strings(live.get("triggers"), "live_update.triggers"))
    if not triggers or not triggers <= {"manual", "successful_save"}:
        raise RecipeError("live_update triggers are unsupported")
    if live.get("formal_requires_saved") is not True:
        raise RecipeError("formal live validation must require saved OA state")
    debounce = live.get("debounce_ms")
    if isinstance(debounce, bool) or not isinstance(debounce, int) or debounce < 0:
        raise RecipeError("live_update.debounce_ms must be a non-negative integer")
    _object(live.get("invalidation"), "live_update.invalidation")


def _validate_publication(value: object, target_value: object) -> None:
    publication = _object(value, "publication")
    output = _object(_object(target_value, "target").get("output_view"), "target.output_view")
    if publication.get("mode") != "ipc_text_view":
        raise RecipeError("publication.mode must be ipc_text_view")
    if output["language"] not in _strings(publication.get("allowed_languages"), "publication.allowed_languages"):
        raise RecipeError("output language is not allowed by publication policy")
    if publication.get("optimistic_concurrency") is not True:
        raise RecipeError("publication must require optimistic concurrency")
    if publication.get("promotion") != "human_approved":
        raise RecipeError("publication promotion must be human_approved")


def _object(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RecipeError(f"{label} must be an object")
    return value


def _text(value: Mapping[str, Any], key: str, label: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result or result != result.strip():
        raise RecipeError(f"{label}.{key} must be non-empty text")
    return result


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _strings(value: object, label: str, *, empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not empty):
        raise RecipeError(f"{label} must be {'an' if empty else 'a non-empty'} array")
    if any(not isinstance(item, str) or not item for item in value):
        raise RecipeError(f"{label} must contain non-empty strings")
    return value


__all__ = ["Recipe", "available_recipes", "load_builtin_recipe", "load_recipe"]
