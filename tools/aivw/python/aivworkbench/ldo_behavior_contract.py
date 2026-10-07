"""Validation for the pending, source-derived LDO behavior contract.

This is the hand-off between exploratory observations and a project-approved
behavior specification.  Candidate domains and metric names are useful for
planning, but every acceptance value remains explicitly pending.  The module
therefore cannot accidentally turn a characterization run into a behavioral
PASS.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import re
from typing import Any, Mapping


class LDOBehaviorContractError(ValueError):
    """Malformed or prematurely qualified LDO behavior contract."""


_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_ROOT = {"schema_version", "kind", "contract_id", "status", "target", "provenance", "behavior", "metrics", "repeat", "holdout", "verdict_policy"}
_TARGET = {"cell", "interface_contract_sha256", "exploratory_policy_sha256"}
_PROVENANCE = {"source", "source_generation", "reference_run", "observation_status"}
_BEHAVIOR = {"supply_window", "dc_vout", "load_dependence", "enable", "transient"}
_WINDOW = {"candidate_probe_domain_v", "accepted_window_v"}
_RANGE = {"min", "max"}
_DC = {"candidate_observations", "formal_relation", "accepted_error_v"}
_LOAD = {"candidate_resistances_ohm", "formal_dependence", "accepted_error_v"}
_ENABLE = {"candidate_voltage_points_v", "active_polarity", "threshold_v", "transition_policy"}
_TRANSIENT = {"candidate_cases", "settling_definition", "accepted_settling_error_s", "accepted_latency_s"}
_METRIC = {"unit", "kind", "accepted_absolute_tolerance", "accepted_relative_tolerance"}
_REPEAT = {"required_runs", "policy"}
_HOLDOUT = {"locked", "public_values_included", "partition_policy"}
_VERDICT = {"behavior_verdict", "correlation_status", "publication"}


def _exact(value: object, fields: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise LDOBehaviorContractError("%s fields are not exactly supported" % label)
    return value


def _id(value: object, label: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise LDOBehaviorContractError("%s must be a bounded identifier" % label)
    return value


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise LDOBehaviorContractError("%s must be a SHA-256 digest" % label)
    return value


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise LDOBehaviorContractError("%s must be finite numeric data" % label)
    return float(value)


def _pending(value: object, label: str) -> None:
    if value is not None:
        raise LDOBehaviorContractError("%s must remain pending" % label)


def _candidate_range(value: object, label: str) -> dict[str, float]:
    raw = _exact(value, _RANGE, label)
    lower = _finite(raw["min"], label + ".min")
    upper = _finite(raw["max"], label + ".max")
    if not lower < upper:
        raise LDOBehaviorContractError("%s must have min < max" % label)
    return {"min": lower, "max": upper}


def _validate_metric(value: object, name: str) -> dict[str, Any]:
    raw = _exact(value, _METRIC, "metrics.%s" % name)
    if raw["kind"] not in {"numeric", "timing", "categorical", "waveform"} or not isinstance(raw["unit"], str) or not raw["unit"]:
        raise LDOBehaviorContractError("metrics.%s identity is malformed" % name)
    _pending(raw["accepted_absolute_tolerance"], "metrics.%s.absolute_tolerance" % name)
    _pending(raw["accepted_relative_tolerance"], "metrics.%s.relative_tolerance" % name)
    return {"unit": raw["unit"], "kind": raw["kind"], "accepted_absolute_tolerance": None, "accepted_relative_tolerance": None}


def validate_behavior_contract(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a normalized pending contract or raise at the contract boundary."""
    root = _exact(value, _ROOT, "behavior contract")
    if root["schema_version"] != 1 or root["kind"] != "ldo-behavior-contract":
        raise LDOBehaviorContractError("unsupported behavior contract schema")
    if root["status"] != "candidate_pending_approval":
        raise LDOBehaviorContractError("behavior contract must remain pending approval")
    contract_id = _id(root["contract_id"], "contract_id")
    target = _exact(root["target"], _TARGET, "target")
    cell = target["cell"]
    if cell not in {"LDO_MASTER", "LDO_AON"}:
        raise LDOBehaviorContractError("unsupported LDO target")
    target_norm = {"cell": cell, "interface_contract_sha256": _digest(target["interface_contract_sha256"], "target.interface_contract_sha256"), "exploratory_policy_sha256": _digest(target["exploratory_policy_sha256"], "target.exploratory_policy_sha256")}
    provenance = _exact(root["provenance"], _PROVENANCE, "provenance")
    if provenance["source"] != "read-only-OA-CDF-TEST_LDO-ADE" or provenance["observation_status"] != "exploratory_only":
        raise LDOBehaviorContractError("provenance must identify read-only exploratory evidence")
    source_generation = _digest(provenance["source_generation"], "provenance.source_generation") if provenance["source_generation"] is not None else None
    if not isinstance(provenance["reference_run"], str) or not provenance["reference_run"]:
        raise LDOBehaviorContractError("provenance.reference_run is required")
    behavior = _exact(root["behavior"], _BEHAVIOR, "behavior")
    window = _exact(behavior["supply_window"], _WINDOW, "behavior.supply_window")
    candidate_window = _candidate_range(window["candidate_probe_domain_v"], "candidate_probe_domain_v")
    _pending(window["accepted_window_v"], "behavior.supply_window.accepted_window_v")
    dc = _exact(behavior["dc_vout"], _DC, "behavior.dc_vout")
    if not isinstance(dc["candidate_observations"], list) or not dc["candidate_observations"] or dc["formal_relation"] is not None:
        raise LDOBehaviorContractError("dc_vout candidate observations/formal relation are invalid")
    _pending(dc["accepted_error_v"], "behavior.dc_vout.accepted_error_v")
    load = _exact(behavior["load_dependence"], _LOAD, "behavior.load_dependence")
    if not isinstance(load["candidate_resistances_ohm"], list) or not load["candidate_resistances_ohm"] or load["formal_dependence"] is not None:
        raise LDOBehaviorContractError("load dependence candidate data are invalid")
    for index, item in enumerate(load["candidate_resistances_ohm"]):
        if _finite(item, "candidate_resistances_ohm[%d]" % index) <= 0.0:
            raise LDOBehaviorContractError("candidate load resistance must be positive")
    _pending(load["accepted_error_v"], "behavior.load_dependence.accepted_error_v")
    enable = _exact(behavior["enable"], _ENABLE, "behavior.enable")
    if cell == "LDO_MASTER":
        if enable["candidate_voltage_points_v"] is not None:
            raise LDOBehaviorContractError("LDO_MASTER cannot declare EN observations")
    else:
        if not isinstance(enable["candidate_voltage_points_v"], list) or not enable["candidate_voltage_points_v"]:
            raise LDOBehaviorContractError("LDO_AON requires EN candidate observations")
        for index, item in enumerate(enable["candidate_voltage_points_v"]):
            _finite(item, "candidate_voltage_points_v[%d]" % index)
    _pending(enable["active_polarity"], "behavior.enable.active_polarity")
    _pending(enable["threshold_v"], "behavior.enable.threshold_v")
    _pending(enable["transition_policy"], "behavior.enable.transition_policy")
    transient = _exact(behavior["transient"], _TRANSIENT, "behavior.transient")
    if not isinstance(transient["candidate_cases"], list) or not transient["candidate_cases"]:
        raise LDOBehaviorContractError("transient candidate cases are required")
    _pending(transient["settling_definition"], "behavior.transient.settling_definition")
    _pending(transient["accepted_settling_error_s"], "behavior.transient.accepted_settling_error_s")
    _pending(transient["accepted_latency_s"], "behavior.transient.accepted_latency_s")
    metrics = root["metrics"]
    if not isinstance(metrics, Mapping) or not metrics:
        raise LDOBehaviorContractError("behavior metrics are required")
    metrics_norm = {str(name): _validate_metric(item, str(name)) for name, item in metrics.items()}
    repeat = _exact(root["repeat"], _REPEAT, "repeat")
    if repeat["required_runs"] != 2 or repeat["policy"] != "repeat_after_contract_approval_only":
        raise LDOBehaviorContractError("repeat policy must remain pending")
    holdout = _exact(root["holdout"], _HOLDOUT, "holdout")
    if holdout != {"locked": True, "public_values_included": False, "partition_policy": "independent_hidden_holdout_required"}:
        raise LDOBehaviorContractError("holdout policy is not locked")
    verdict = _exact(root["verdict_policy"], _VERDICT, "verdict_policy")
    if verdict != {"behavior_verdict": "NOT_ESTABLISHED", "correlation_status": "BLOCKED_CONTRACT", "publication": "human_approval_required"}:
        raise LDOBehaviorContractError("pending verdict policy was weakened")
    return {"schema_version": 1, "kind": "ldo-behavior-contract", "contract_id": contract_id,
            "status": "candidate_pending_approval", "target": target_norm,
            "provenance": {"source": provenance["source"], "source_generation": source_generation,
                           "reference_run": provenance["reference_run"], "observation_status": "exploratory_only"},
            "behavior": {"supply_window": {"candidate_probe_domain_v": candidate_window, "accepted_window_v": None},
                         "dc_vout": {"candidate_observations": list(dc["candidate_observations"]), "formal_relation": None, "accepted_error_v": None},
                         "load_dependence": {"candidate_resistances_ohm": list(load["candidate_resistances_ohm"]), "formal_dependence": None, "accepted_error_v": None},
                         "enable": {"candidate_voltage_points_v": enable["candidate_voltage_points_v"], "active_polarity": None, "threshold_v": None, "transition_policy": None},
                         "transient": {"candidate_cases": list(transient["candidate_cases"]), "settling_definition": None, "accepted_settling_error_s": None, "accepted_latency_s": None}},
            "metrics": metrics_norm,
            "repeat": {"required_runs": 2, "policy": "repeat_after_contract_approval_only"},
            "holdout": dict(holdout), "verdict_policy": dict(verdict)}


def read_behavior_contract(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LDOBehaviorContractError("cannot read behavior contract: %s" % exc) from exc
    if not isinstance(value, Mapping):
        raise LDOBehaviorContractError("behavior contract root must be an object")
    return validate_behavior_contract(value)


__all__ = ["LDOBehaviorContractError", "validate_behavior_contract", "read_behavior_contract"]
