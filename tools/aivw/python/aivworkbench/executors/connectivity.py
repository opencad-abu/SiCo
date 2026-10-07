"""Executor for the deterministic official-SI connectivity gate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ..connectivity import (
    ConnectivityParseError,
    Structure,
    compare_structures,
    parse_globalmap,
    parse_globalmap_models,
    parse_inherited_connections,
    parse_si_map,
    parse_si_netlists,
    parse_verilog,
)
from ..executor import ExecutorContext, ExecutorResult
from ..workspace import sha256_file, write_json_once


def run_connectivity_check(context: ExecutorContext) -> ExecutorResult:
    """Compare a generated structural assembly with Cadence's SI inventory.

    ``ai.generate_model`` must explicitly identify the candidate structural
    assembly using ``connectivity_source``.  A behavioral leaf model alone is
    never silently treated as a hierarchy/net binding; this is a fail-closed
    boundary between model generation and structural truth.
    """
    structure_contract = context.recipe.payload.get("structure")
    if not isinstance(structure_contract, Mapping) or structure_contract.get("provider") != "cadence_si":
        return _blocked(
            "structure_provider_unsupported",
            "connectivity.check currently requires the cadence_si artifact contract",
        )
    structure_result = context.dependencies.get("structure")
    if structure_result is None or structure_result.status != "PASS":
        return _blocked("structure_dependency", "structure dependency did not PASS")
    if structure_result.outputs.get("structure_authoritative") is not True:
        return _blocked("structure_not_authoritative", "structure is not authoritative")
    try:
        generated = _generation_result(context.dependencies)
    except ValueError as exc:
        return _blocked("connectivity_source_ambiguous", str(exc))
    candidate_ref = generated.outputs.get("connectivity_source")
    if not isinstance(candidate_ref, str) or not candidate_ref:
        return _blocked(
            "connectivity_source_missing",
            "generation must declare outputs.connectivity_source",
        )
    try:
        candidate_path = _payload_file(context, candidate_ref)
        netlist_refs = structure_result.outputs.get("netlist")
        if not isinstance(netlist_refs, list) or not netlist_refs:
            raise ConnectivityParseError("structure output has no netlist inventory")
        official_texts = [
            _payload_file(context, value).read_text(encoding="utf-8", errors="replace")
            for value in netlist_refs
            if isinstance(value, str)
        ]
        if len(official_texts) != len(netlist_refs):
            raise ConnectivityParseError("structure netlist inventory contains non-text path")
        official_modules = parse_si_netlists(official_texts)
        map_path = _payload_file(context, structure_result.outputs.get("map"))
        globalmap_path = _payload_file(context, structure_result.outputs.get("globalmap"))
        aliases = parse_si_map(map_path.read_text(encoding="utf-8", errors="replace"))
        # ``run/map/current`` normally carries only global aliases; per-module
        # maps carry top-level aliases (for example Din+ -> cdsNet1).  Include
        # every authoritative SI map adjacent to the inventoried netlists.
        declared_module_maps = structure_result.outputs.get("module_maps", [])
        if not isinstance(declared_module_maps, list) or any(
            not isinstance(item, str) for item in declared_module_maps
        ):
            raise ConnectivityParseError("structure module map inventory is invalid")
        module_map_paths = {
            _payload_file(context, item).parent: _payload_file(context, item)
            for item in declared_module_maps
        }
        module_aliases: dict[str, dict[str, str]] = {}
        for netlist_ref in netlist_refs:
            if not isinstance(netlist_ref, str):
                continue
            netlist_path = _payload_file(context, netlist_ref)
            local_map = module_map_paths.get(netlist_path.parent)
            if local_map is not None:
                modules = parse_verilog(netlist_path.read_text(encoding="utf-8", errors="replace"))
                if len(modules) != 1:
                    raise ConnectivityParseError("SI inventory entry must contain exactly one module")
                module_aliases[modules[0].name] = dict(
                    parse_si_map(local_map.read_text(encoding="utf-8", errors="replace"))
                )
        globalmap_text = globalmap_path.read_text(encoding="utf-8", errors="replace")
        global_aliases = parse_globalmap(globalmap_text)
        model_bindings = parse_globalmap_models(globalmap_text)
        if not set(global_aliases).issubset(set(aliases)):
            raise ConnectivityParseError("SI map and globalmap global aliases disagree")
        candidate_modules = parse_verilog(
            candidate_path.read_text(encoding="utf-8", errors="replace")
        )
        expected_module_names = {module.name for module in official_modules.modules}
        mapped_module_names = {module for _oa_view, module in model_bindings}
        if not expected_module_names.issubset(mapped_module_names):
            raise ConnectivityParseError("globalmap model bindings omit an SI module")
        inherited_ref = structure_result.outputs.get("inherited_connections")
        inherited_connections: tuple[tuple[str, str, str, str, str], ...] = ()
        if isinstance(inherited_ref, str):
            inherited_connections = parse_inherited_connections(
                _payload_file(context, inherited_ref).read_text(encoding="utf-8", errors="replace")
            )
    except (OSError, TypeError, ValueError, ConnectivityParseError) as exc:
        return _blocked("connectivity_input_invalid", str(exc))

    contract = _load_contract(context)
    expected_module = str(context.recipe.target.get("module", ""))
    top_aliases = tuple(sorted(module_aliases.get(expected_module, {}).items()))
    expected_ports = _mapped_contract_ports(_contract_port_order(contract), top_aliases)
    raw_globals = generated.outputs.get("global_mapping")
    candidate_globals = _global_pairs(raw_globals)
    official = Structure(official_modules.modules, globals=global_aliases, aliases=aliases)
    candidate = Structure(candidate_modules, globals=candidate_globals)
    findings = compare_structures(
        official,
        candidate,
        expected_module=expected_module,
        expected_ports=expected_ports or None,
        expected_globals=global_aliases,
        module_aliases=module_aliases,
    )
    report_path = context.gate_root / "connectivity-report.json"
    evidence_path = context.gate_root / "connectivity-evidence.json"
    report = {
        "schema_version": 1,
        "status": "PASS" if not findings else "FAIL",
        "authority": "cadence_si_netlist_map_globalmap",
        "candidate": str(candidate_path.relative_to(context.run.payload_root)),
        "candidate_sha256": sha256_file(candidate_path),
        "official_netlists": [str(item) for item in netlist_refs],
        "official_map": str(map_path.relative_to(context.run.payload_root)),
        "official_globalmap": str(globalmap_path.relative_to(context.run.payload_root)),
        "official_module_maps": [
            str(path.relative_to(context.run.payload_root))
            for path in sorted(module_map_paths.values())
        ],
        "findings": [item.as_dict() for item in findings],
        "aliases": [list(item) for item in aliases],
        "module_aliases": {
            name: [[source, target] for source, target in sorted(values.items())]
            for name, values in sorted(module_aliases.items())
        },
        "global_aliases": [list(item) for item in global_aliases],
        "candidate_global_mapping": [list(item) for item in candidate_globals],
        "model_bindings": [list(item) for item in model_bindings],
        "inherited_connections": [list(item) for item in inherited_connections],
        "expected_module": expected_module,
        "expected_ports": list(expected_ports),
    }
    evidence = {
        "schema_version": 1,
        "status": report["status"],
        "source_generation": structure_result.outputs.get("source_generation"),
        "structure_authoritative": True,
        "report_sha256": None,
    }
    write_json_once(report_path, report)
    evidence["report_sha256"] = sha256_file(report_path)
    write_json_once(evidence_path, evidence)
    artifacts = (
        report_path,
        evidence_path,
        candidate_path,
        *( _payload_file(context, value) for value in netlist_refs ),
        *module_map_paths.values(),
        map_path,
        globalmap_path,
        *(
            (_payload_file(context, structure_result.outputs["inherited_connections"]),)
            if isinstance(structure_result.outputs.get("inherited_connections"), str)
            else ()
        ),
    )
    if findings:
        return ExecutorResult(
            "FAIL_CONNECTIVITY",
            {"code": "connectivity_mismatch", "finding_count": len(findings), "findings": [item.as_dict() for item in findings]},
            {"connectivity_report": str(report_path.relative_to(context.run.payload_root)), "connectivity_evidence": str(evidence_path.relative_to(context.run.payload_root)), "connectivity_pass": False},
            artifacts,
        )
    return ExecutorResult(
        "PASS",
        {"code": "connectivity_match", "finding_count": 0, "findings": []},
        {"connectivity_report": str(report_path.relative_to(context.run.payload_root)), "connectivity_evidence": str(evidence_path.relative_to(context.run.payload_root)), "connectivity_pass": True},
        artifacts,
    )


def _generation_result(dependencies: Mapping[str, Any]):
    candidates = [value for value in dependencies.values() if "connectivity_source" in value.outputs]
    if len(candidates) != 1:
        raise ValueError(f"connectivity check requires exactly one connectivity source, found {len(candidates)}")
    return candidates[0]


def _payload_file(context: ExecutorContext, value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise ConnectivityParseError("missing artifact path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ConnectivityParseError(f"unsafe artifact path: {value}")
    candidate = context.run.payload_root / relative
    resolved = candidate.resolve()
    if candidate.is_symlink() or not resolved.is_file() or not resolved.is_relative_to(context.run.payload_root.resolve()):
        raise ConnectivityParseError(f"artifact unavailable or outside payload: {value}")
    return resolved


def _load_contract(context: ExecutorContext) -> Mapping[str, Any]:
    path = context.recipe.input_path("interface_contract")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConnectivityParseError(f"interface contract unavailable: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ConnectivityParseError("interface contract root is not an object")
    return value


def _contract_port_order(contract: Mapping[str, Any]) -> tuple[str, ...]:
    interface = contract.get("interface")
    if not isinstance(interface, Mapping):
        return ()
    order = interface.get("port_order")
    if isinstance(order, list) and all(isinstance(item, str) for item in order):
        return tuple(order)
    return ()


def _mapped_contract_ports(
    order: tuple[str, ...], aliases: tuple[tuple[str, str], ...]
) -> tuple[str, ...]:
    mapping = dict(aliases)
    return tuple(mapping.get(name, name) for name in order)


def _global_pairs(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        return ()
    pairs: list[tuple[str, str]] = []
    for item in value:
        if isinstance(item, (list, tuple)) and len(item) == 2 and all(isinstance(part, str) for part in item):
            pairs.append((item[0], item[1]))
        elif isinstance(item, Mapping) and isinstance(item.get("source"), str) and isinstance(item.get("target"), str):
            pairs.append((str(item["source"]), str(item["target"])))
    return tuple(sorted(set(pairs)))


def _blocked(code: str, detail: str) -> ExecutorResult:
    return ExecutorResult("BLOCKED_INPUT", {"code": code, "detail": detail}, {"connectivity_pass": False})


__all__ = ["run_connectivity_check"]
