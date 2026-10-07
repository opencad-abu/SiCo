"""Build and validate the source-derived LDO calibration hand-off.

The hand-off is an evidence index between exploratory Spectre observations and
the project-approved behavior contract.  It deliberately contains observed
domains and ranges only.  It cannot carry an acceptance tolerance or a design
verdict, and it never mutates an exploratory payload.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from .manifest import verify_split_manifests
from .workspace import sha256_file


class LDOCalibrationError(ValueError):
    """Malformed, incomplete, or prematurely qualified calibration hand-off."""


_HEX64 = re.compile(r"^[a-f0-9]{64}$")
_RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{6}\.[0-9]{6}Z-recipe-run-[0-9a-f]{12}$")
_TOPOLOGIES = {"LDO_MASTER", "LDO_AON"}
_ROOT_FIELDS = {
    "schema_version", "kind", "handoff_id", "status", "source", "runs",
    "contract_items_pending", "verdict_policy", "handoff_digest",
}
_SOURCE_FIELDS = {
    "interface_contract_sha256", "experiment_contract_sha256", "model_file",
    "model_section", "temperature_c", "analysis", "dataset",
}
_RUN_FIELDS = {
    "topology", "recipe_id", "run_id", "control_manifest", "payload_manifest",
    "plan", "observations", "source_generation", "source_generation_before",
    "source_generation_after", "case_count", "psf_artifact_count", "case_ids",
    "stimulus_domains", "observed_ranges", "measurement_contract", "model_files",
}
_ARTIFACT_FIELDS = {"path", "sha256"}
_DOMAINS = ("vdd_v", "load_resistance_ohm", "en_v")
_RANGE_GROUPS = ("dc", "transient")
_RANGE_SIGNALS = ("VOUT", "IDD")
_MEASUREMENT_FIELDS = {"signals", "time_unit", "sample_times_s", "extraction", "tolerance_status"}
_INTERFACE_CONTRACT_SHA256 = "ffbe024af0917ad8e91a922c71e075c9042ffb21bda4e000d2a70375c17d3873"
_EXPERIMENT_CONTRACT_SHA256 = "dea1b33d248c344f4831b389a6a7be1b0270eee10653df734e5d985adb413361"


def _exact(value: object, fields: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise LDOCalibrationError("%s fields are not exactly supported" % label)
    return value


def _sha(value: object, label: str) -> str:
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        raise LDOCalibrationError("%s must be a SHA-256 digest" % label)
    return value


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise LDOCalibrationError("%s must be finite numeric data" % label)
    return float(value)


def _canonical_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _artifact(path: object, digest: object, label: str) -> dict[str, str]:
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise LDOCalibrationError("%s.path must be absolute" % label)
    parts = Path(path).parts
    if len(parts) >= 4 and parts[1] == "home" and parts[3] == "simulation":
        raise LDOCalibrationError("%s.path may not use the default Cadence output root" % label)
    return {"path": path, "sha256": _sha(digest, label + ".sha256")}


def _read_json(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LDOCalibrationError("cannot read %s: %s" % (label, exc)) from exc
    if not isinstance(value, Mapping):
        raise LDOCalibrationError("%s must be a JSON object" % label)
    return value


def _file_artifact(path: Path, label: str) -> dict[str, str]:
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise LDOCalibrationError("%s is missing, non-absolute, or a symlink" % label)
    return _artifact(str(path), sha256_file(path), label)


def _verify_case_artifact(payload_root: Path, case: Mapping[str, Any], key: str, hash_key: str) -> None:
    relative = case.get(key)
    declared = case.get(hash_key)
    if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise LDOCalibrationError("case %s path is unsafe" % key)
    path = (payload_root / relative).resolve(strict=False)
    if not path.is_file() or path.is_symlink() or not path.is_relative_to(payload_root.resolve()):
        raise LDOCalibrationError("case %s is missing or outside payload" % key)
    if not isinstance(declared, str) or declared != sha256_file(path):
        raise LDOCalibrationError("case %s hash does not match its bound file" % key)


def _verify_derived_case_artifact(payload_root: Path, case: Mapping[str, Any], filename: str, hash_key: str) -> None:
    case_id = case.get("id")
    if not isinstance(case_id, str) or Path(case_id).name != case_id or ".." in Path(case_id).parts:
        raise LDOCalibrationError("case ID is unsafe")
    path = (payload_root / "checks" / "gates" / "exploratory" / "cases" / case_id / filename).resolve(strict=False)
    if not path.is_file() or path.is_symlink() or not path.is_relative_to(payload_root.resolve()):
        raise LDOCalibrationError("case %s is missing or outside payload" % filename)
    declared = case.get(hash_key)
    if not isinstance(declared, str) or declared != sha256_file(path):
        raise LDOCalibrationError("case %s hash does not match its bound file" % filename)


def _verify_case_psf(payload_root: Path, case: Mapping[str, Any]) -> None:
    records = case.get("psf_artifacts")
    if not isinstance(records, list) or not records:
        raise LDOCalibrationError("case PSF artifact index is missing")
    for index, record in enumerate(records):
        raw = _exact(record, {"path", "sha256", "size"}, "psf artifact %d" % index)
        relative = raw["path"]
        if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise LDOCalibrationError("PSF artifact path is unsafe")
        path = (payload_root / relative).resolve(strict=False)
        if not path.is_file() or path.is_symlink() or not path.is_relative_to(payload_root.resolve()):
            raise LDOCalibrationError("PSF artifact is missing or outside payload")
        if not isinstance(raw["size"], int) or raw["size"] != path.stat().st_size or raw["sha256"] != sha256_file(path):
            raise LDOCalibrationError("PSF artifact hash/size does not match its bound file")


def _value_domain(cases: Sequence[Mapping[str, Any]], key: str) -> list[float]:
    values = []
    for case in cases:
        stimulus = case.get("stimulus")
        if isinstance(stimulus, Mapping) and key in stimulus:
            values.append(_finite(stimulus[key], "stimulus.%s" % key))
    return sorted(set(values))


def _range(cases: Sequence[Mapping[str, Any]], signal: str) -> list[float]:
    values: list[float] = []
    for case in cases:
        measurements = case.get("measurements")
        if not isinstance(measurements, Mapping):
            raise LDOCalibrationError("case measurements are missing")
        extrema = measurements.get("extrema")
        if not isinstance(extrema, Mapping) or signal not in extrema:
            raise LDOCalibrationError("case measurements are missing %s extrema" % signal)
        item = _exact(extrema[signal], {"min", "max"}, "measurements.extrema.%s" % signal)
        values.extend((_finite(item["min"], signal + ".min"), _finite(item["max"], signal + ".max")))
    if not values:
        raise LDOCalibrationError("no observations for %s" % signal)
    return [min(values), max(values)]


def _build_run(spec: Mapping[str, Any]) -> dict[str, Any]:
    required = {"topology", "recipe_id", "run_id", "control_manifest", "payload_manifest"}
    if not required.issubset(spec):
        raise LDOCalibrationError("run specification is missing required paths")
    topology = spec["topology"]
    if topology not in _TOPOLOGIES:
        raise LDOCalibrationError("unsupported calibration topology")
    run_id = spec["run_id"]
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise LDOCalibrationError("run_id is malformed")
    control_manifest = Path(str(spec["control_manifest"])).expanduser().resolve()
    payload_manifest = Path(str(spec["payload_manifest"])).expanduser().resolve()
    if verify_split_manifests(control_manifest, payload_manifest).get("status") != "PASS":
        raise LDOCalibrationError("split manifest is not PASS")
    control = _read_json(control_manifest, "control manifest")
    payload = _read_json(payload_manifest, "payload manifest")
    if control.get("run_id") != run_id or payload.get("run_id") != run_id:
        raise LDOCalibrationError("manifest run_id does not match hand-off run_id")
    payload_root = payload_manifest.parent
    evidence_path = payload_root / "checks/gates/exploratory/exploratory-observations.json"
    plan_path = payload_root / "checks/gates/exploratory/exploratory-plan.json"
    evidence = _read_json(evidence_path, "exploratory observations")
    plan = _read_json(plan_path, "exploratory plan")
    if evidence.get("topology") != topology or plan.get("topology") != topology:
        raise LDOCalibrationError("exploratory topology does not match hand-off")
    if evidence.get("interface_contract_sha256") != _INTERFACE_CONTRACT_SHA256 or evidence.get("experiment_contract_sha256") != _EXPERIMENT_CONTRACT_SHA256:
        raise LDOCalibrationError("exploratory evidence is bound to a different source contract")
    plan_digest = plan.get("plan_digest")
    if not isinstance(plan_digest, str) or plan_digest != _canonical_digest({key: value for key, value in plan.items() if key != "plan_digest"}):
        raise LDOCalibrationError("exploratory plan digest is invalid")
    if evidence.get("source_generation") != evidence.get("source_generation_before") or evidence.get("source_generation") != evidence.get("source_generation_after"):
        raise LDOCalibrationError("source generation changed during exploratory run")
    cases = evidence.get("cases")
    plan_cases = plan.get("cases")
    if not isinstance(plan_cases, list) or not isinstance(cases, list) or not cases or len(cases) != len(plan_cases):
        raise LDOCalibrationError("exploratory case set is incomplete")
    if any(not isinstance(case, Mapping) or case.get("status") != "OBSERVED" for case in cases):
        raise LDOCalibrationError("all calibration cases must be OBSERVED")
    plan_ids = [case.get("id") for case in plan_cases]
    case_ids = [case.get("id") for case in cases]
    if case_ids != plan_ids or any(not isinstance(item, str) or not item for item in case_ids):
        raise LDOCalibrationError("exploratory case IDs do not match the plan")
    if evidence.get("behavior_verdict") != "NOT_ESTABLISHED" or evidence.get("correlation_status") != "BLOCKED_CONTRACT":
        raise LDOCalibrationError("exploratory evidence has a premature verdict")
    if evidence.get("tolerance_evaluation") != "not_performed":
        raise LDOCalibrationError("calibration hand-off cannot include tolerance evaluation")
    measurement = _exact(evidence.get("measurement_contract"), _MEASUREMENT_FIELDS, "measurement_contract")
    if measurement.get("tolerance_status") != "BLOCKED_CONTRACT":
        raise LDOCalibrationError("measurement tolerance contract is not blocked")
    dc = [case for case in cases if case.get("kind") == "dc"]
    transient = [case for case in cases if case.get("kind") == "transient"]
    if not dc or not transient:
        raise LDOCalibrationError("calibration requires DC and transient observations")
    for case in cases:
        if case.get("kind") not in {"dc", "transient"}:
            raise LDOCalibrationError("exploratory case kind is unsupported")
        _verify_case_artifact(payload_root, case, "deck", "deck_sha256")
        _verify_case_artifact(payload_root, case, "csv", "csv_sha256")
        _verify_case_artifact(payload_root, case, "spectre_log", "spectre_log_sha256")
        _verify_case_artifact(payload_root, case, "ocean_log", "ocean_log_sha256")
        _verify_derived_case_artifact(payload_root, case, "console.log", "console_log_sha256")
        _verify_derived_case_artifact(payload_root, case, "ocean.console.log", "ocean_console_log_sha256")
        _verify_case_psf(payload_root, case)
    model_files = evidence.get("model_files")
    if not isinstance(model_files, list) or not model_files:
        raise LDOCalibrationError("staged model evidence is missing")
    normalized_models = []
    for item in model_files:
        raw = _exact(item, {"source", "relative_path", "sha256", "size", "staged_sha256", "staged_size"}, "model file")
        normalized_models.append({"relative_path": str(raw["relative_path"]), "sha256": _sha(raw["sha256"], "model.sha256"), "staged_sha256": _sha(raw["staged_sha256"], "model.staged_sha256"), "size": int(raw["size"]), "staged_size": int(raw["staged_size"])})
    ranges = {group: {signal: _range(dc if group == "dc" else transient, signal) for signal in _RANGE_SIGNALS} for group in _RANGE_GROUPS}
    return {
        "topology": topology,
        "recipe_id": str(spec["recipe_id"]),
        "run_id": run_id,
        "control_manifest": _file_artifact(control_manifest, "control_manifest"),
        "payload_manifest": _file_artifact(payload_manifest, "payload_manifest"),
        "plan": {"path": str(plan_path), "sha256": sha256_file(plan_path), "digest": str(plan_digest)},
        "observations": _file_artifact(evidence_path, "observations"),
        "source_generation": _sha(evidence["source_generation"], "source_generation"),
        "source_generation_before": _sha(evidence["source_generation_before"], "source_generation_before"),
        "source_generation_after": _sha(evidence["source_generation_after"], "source_generation_after"),
        "case_count": len(cases),
        "psf_artifact_count": sum(len(case.get("psf_artifacts", [])) for case in cases),
        "case_ids": [str(case["id"]) for case in cases],
        "stimulus_domains": {key: _value_domain(cases, key) for key in _DOMAINS},
        "observed_ranges": ranges,
        "measurement_contract": {key: measurement[key] for key in _MEASUREMENT_FIELDS},
        "model_files": normalized_models,
    }


def build_calibration_handoff(specs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Build one deterministic hand-off from already verified run evidence."""
    if len(specs) != 2:
        raise LDOCalibrationError("handoff requires exactly MASTER and AON runs")
    runs = [_build_run(spec) for spec in specs]
    if {run["topology"] for run in runs} != _TOPOLOGIES:
        raise LDOCalibrationError("handoff requires one run for each LDO topology")
    runs.sort(key=lambda item: item["topology"])
    result = {
        "schema_version": 1,
        "kind": "ldo-behavior-calibration-handoff",
        "handoff_id": "amsLDO.behavior-calibration.handoff.v1",
        "status": "observed_pending_contract",
        "source": {
            "interface_contract_sha256": _INTERFACE_CONTRACT_SHA256,
            "experiment_contract_sha256": _EXPERIMENT_CONTRACT_SHA256,
            "model_file": "gpdk045.scs",
            "model_section": "tt",
            "temperature_c": 27.0,
            "analysis": "tran",
            "dataset": "exploratory_observation",
        },
        "runs": runs,
        "contract_items_pending": [
            "supply_window", "dc_vout_relation", "load_dependence", "aon_en_polarity",
            "aon_en_threshold", "transition_policy", "settling_definition",
            "vout_tolerance", "supply_current_tolerance", "latency_tolerance",
            "repeat_policy", "hidden_holdout", "spectre_rnm_correlation",
        ],
        "verdict_policy": {
            "behavior_verdict": "NOT_ESTABLISHED",
            "correlation_status": "BLOCKED_CONTRACT",
            "tolerance_evaluation": "not_performed",
            "publication": "human_approval_required",
        },
    }
    result["handoff_digest"] = _canonical_digest({key: value for key, value in result.items() if key != "handoff_digest"})
    return result


def validate_calibration_handoff(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a hand-off without touching or rewriting its source payloads."""
    root = _exact(value, _ROOT_FIELDS, "calibration handoff")
    if root["schema_version"] != 1 or root["kind"] != "ldo-behavior-calibration-handoff" or root["status"] != "observed_pending_contract":
        raise LDOCalibrationError("calibration hand-off must remain observation-only")
    if not isinstance(root["handoff_id"], str) or not root["handoff_id"]:
        raise LDOCalibrationError("handoff_id is required")
    source = _exact(root["source"], _SOURCE_FIELDS, "source")
    _sha(source["interface_contract_sha256"], "source.interface_contract_sha256")
    _sha(source["experiment_contract_sha256"], "source.experiment_contract_sha256")
    if source["model_file"] != "gpdk045.scs" or source["model_section"] != "tt" or source["analysis"] != "tran" or source["dataset"] != "exploratory_observation" or _finite(source["temperature_c"], "source.temperature_c") != 27.0:
        raise LDOCalibrationError("source calibration identity is not approved")
    runs = root["runs"]
    if not isinstance(runs, list) or len(runs) != 2 or {item.get("topology") for item in runs if isinstance(item, Mapping)} != _TOPOLOGIES:
        raise LDOCalibrationError("handoff must contain exactly MASTER and AON runs")
    for item in runs:
        raw = _exact(item, _RUN_FIELDS, "run")
        if raw["topology"] not in _TOPOLOGIES or not _RUN_ID.fullmatch(str(raw["run_id"])):
            raise LDOCalibrationError("run identity is malformed")
        for name in ("control_manifest", "payload_manifest", "observations"):
            record = raw[name]
            if not isinstance(record, Mapping):
                raise LDOCalibrationError("%s artifact record is malformed" % name)
            _artifact(record["path"], record["sha256"], name)
            path = Path(record["path"])
            if not path.is_file() or path.is_symlink() or sha256_file(path) != record["sha256"]:
                raise LDOCalibrationError("%s hash does not match its bound file" % name)
        if not isinstance(raw["plan"], Mapping) or not isinstance(raw["plan"].get("path"), str) or not Path(raw["plan"]["path"]).is_absolute():
            raise LDOCalibrationError("plan path must be absolute")
        _sha(raw["plan"]["sha256"], "plan.sha256")
        if not isinstance(raw["plan"]["digest"], str) or not raw["plan"]["digest"].startswith("sha256:"):
            raise LDOCalibrationError("plan digest is malformed")
        plan_path = Path(raw["plan"]["path"])
        if not plan_path.is_file() or plan_path.is_symlink() or sha256_file(plan_path) != raw["plan"]["sha256"]:
            raise LDOCalibrationError("plan hash does not match its bound file")
        plan_value = _read_json(plan_path, "bound exploratory plan")
        if plan_value.get("plan_digest") != raw["plan"]["digest"]:
            raise LDOCalibrationError("bound exploratory plan digest does not match")
        for name in ("source_generation", "source_generation_before", "source_generation_after"):
            _sha(raw[name], name)
        if len({raw["source_generation"], raw["source_generation_before"], raw["source_generation_after"]}) != 1:
            raise LDOCalibrationError("source generations are not stable")
        if not isinstance(raw["case_ids"], list) or len(raw["case_ids"]) != raw["case_count"] or len(set(raw["case_ids"])) != raw["case_count"] or raw["case_count"] < 1:
            raise LDOCalibrationError("case IDs/count are inconsistent")
        if not isinstance(raw["psf_artifact_count"], int) or raw["psf_artifact_count"] < raw["case_count"]:
            raise LDOCalibrationError("PSF artifact count is incomplete")
        domains = raw["stimulus_domains"]
        if not isinstance(domains, Mapping) or set(domains) != set(_DOMAINS):
            raise LDOCalibrationError("stimulus domains are incomplete")
        for key in _DOMAINS:
            if not isinstance(domains[key], list) or any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(float(v)) for v in domains[key]):
                raise LDOCalibrationError("stimulus domain %s is malformed" % key)
        ranges = raw["observed_ranges"]
        if not isinstance(ranges, Mapping) or set(ranges) != set(_RANGE_GROUPS):
            raise LDOCalibrationError("observed ranges are incomplete")
        for group in _RANGE_GROUPS:
            for signal in _RANGE_SIGNALS:
                values = ranges[group].get(signal) if isinstance(ranges[group], Mapping) else None
                if not isinstance(values, list) or len(values) != 2 or any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(float(v)) for v in values) or values[0] > values[1]:
                    raise LDOCalibrationError("observed range %s.%s is malformed" % (group, signal))
        measurement = _exact(raw["measurement_contract"], _MEASUREMENT_FIELDS, "measurement_contract")
        if measurement["time_unit"] != "s" or measurement["tolerance_status"] != "BLOCKED_CONTRACT":
            raise LDOCalibrationError("measurement contract is not observation-only")
        if not isinstance(raw["model_files"], list) or not raw["model_files"]:
            raise LDOCalibrationError("model file evidence is missing")
        rebuilt = _build_run({
            "topology": raw["topology"],
            "recipe_id": raw["recipe_id"],
            "run_id": raw["run_id"],
            "control_manifest": raw["control_manifest"]["path"],
            "payload_manifest": raw["payload_manifest"]["path"],
        })
        if rebuilt != dict(raw):
            raise LDOCalibrationError("calibration hand-off summary does not match bound evidence")
    pending = root["contract_items_pending"]
    if not isinstance(pending, list) or not pending or any(not isinstance(item, str) or not item for item in pending):
        raise LDOCalibrationError("pending contract item list is malformed")
    verdict = _exact(root["verdict_policy"], {"behavior_verdict", "correlation_status", "tolerance_evaluation", "publication"}, "verdict_policy")
    if verdict != {"behavior_verdict": "NOT_ESTABLISHED", "correlation_status": "BLOCKED_CONTRACT", "tolerance_evaluation": "not_performed", "publication": "human_approval_required"}:
        raise LDOCalibrationError("calibration verdict policy was weakened")
    expected = _canonical_digest({key: root[key] for key in root if key != "handoff_digest"})
    if root["handoff_digest"] != expected:
        raise LDOCalibrationError("handoff digest does not match content")
    return dict(root)


def read_calibration_handoff(path: Path) -> dict[str, Any]:
    return validate_calibration_handoff(_read_json(path, "calibration hand-off"))


__all__ = [
    "LDOCalibrationError", "build_calibration_handoff", "validate_calibration_handoff",
    "read_calibration_handoff",
]
