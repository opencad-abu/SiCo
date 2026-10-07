"""Offline deterministic ``ldo.linear_regulator`` model-class plugin.

The plugin is a registered scaffold used to exercise the M1 pipeline.  It
renders bounded SystemVerilog text from the canonical contract/spec and never
claims a design verdict.  Correlation remains the responsibility of
``ldo_metrics`` and the workflow gates.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Mapping

from ..ldo import LDOInterface, canonicalize_interface
from ..l1 import build_l1_test_plan
from ..model import ModelGenerationRequest


_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


@dataclass(frozen=True)
class LinearRegulatorCandidate:
    source: str
    smoke_testbench: str
    model_version: str
    module: str
    test_plan: Mapping[str, Any]
    interface: Mapping[str, Any]
    capabilities: tuple[str, ...] = (
        "dc-transfer", "supply-window", "optional-enable", "transient", "sampled-rnm"
    )


def generate_linear_regulator_candidate(request: ModelGenerationRequest) -> LinearRegulatorCandidate:
    contract = request.contract
    spec = request.spec
    if spec.get("status") == "blocked_pending_block_characterization":
        raise ValueError("LDO behavior requires block characterization and an acceptance tolerance contract")
    interface = _extract_interface(contract, spec)
    module = _module_name(spec, contract, interface)
    behavior = spec.get("behavior", {}) if isinstance(spec, Mapping) else {}
    parameters = spec.get("parameters", {}) if isinstance(spec, Mapping) else {}
    window = _window(spec, parameters)
    gain = _parameter(parameters, "gain", 0.5, 0.0, 4.0)
    tau = _parameter(parameters, "tau_seconds", 0.0, 0.0, 10.0)
    test_plan = build_l1_test_plan(request.verification) if request.verification else {
        "schema_version": 1, "protocol": "AIVW_L1_EVENT v1 JSONL", "cases": [], "required_metrics": []
    }
    source = _render_sv(module, interface, window, gain, tau, behavior)
    smoke = _render_smoke(module, interface)
    return LinearRegulatorCandidate(
        source=source,
        smoke_testbench=smoke,
        model_version="aivw-m1-ldo-rule-1",
        module=module,
        test_plan=test_plan,
        interface=interface.to_dict(),
    )


def _extract_interface(contract: Mapping[str, Any], spec: Mapping[str, Any]) -> LDOInterface:
    # Contracts may be either the canonical interface object or a full
    # contract containing an ``interface`` member.
    value = contract.get("interface", contract) if isinstance(contract, Mapping) else contract
    target = spec.get("target", {}) if isinstance(spec, Mapping) else {}
    cell = spec.get("cell") if isinstance(spec, Mapping) else None
    if cell is None and isinstance(target, Mapping):
        cell = target.get("cell")
    if cell is None and isinstance(contract, Mapping):
        contract_target = contract.get("target")
        if isinstance(contract_target, Mapping):
            cell = contract_target.get("cell")
    if isinstance(value, Mapping) and "variants" not in value:
        value = dict(value)
        if isinstance(cell, str):
            value.setdefault("cell", cell)
    return canonicalize_interface(value, cell=cell if isinstance(cell, str) else None)


def _module_name(spec: Mapping[str, Any], contract: Mapping[str, Any], interface: LDOInterface) -> str:
    target = spec.get("target", {}) if isinstance(spec, Mapping) else {}
    value = target.get("module") if isinstance(target, Mapping) else None
    if not isinstance(value, str) or not value:
        value = contract.get("module") if isinstance(contract, Mapping) else None
    if not isinstance(value, str) or not value:
        value = interface.cell
    if not _IDENT.fullmatch(value):
        raise ValueError("LDO target module is invalid")
    return value


def _window(spec: Mapping[str, Any], parameters: Mapping[str, Any]) -> tuple[float, float]:
    source = parameters.get("supply_window") if isinstance(parameters, Mapping) else None
    if source is None and isinstance(spec, Mapping):
        source = spec.get("supply_window")
    if isinstance(source, Mapping):
        low, high = source.get("min"), source.get("max")
    elif isinstance(source, (list, tuple)) and len(source) == 2:
        low, high = source
    else:
        low, high = 0.0, 100.0
    low = _number(low, "supply_window.min")
    high = _number(high, "supply_window.max")
    if low >= high:
        raise ValueError("supply window must be increasing")
    return low, high


def _parameter(parameters: Mapping[str, Any], name: str, default: float, lower: float, upper: float) -> float:
    value = parameters.get(name, default) if isinstance(parameters, Mapping) else default
    if isinstance(value, Mapping):
        value = value.get("value", value.get("default", default))
    result = _number(value, name)
    if result < lower or result > upper:
        raise ValueError("%s is outside plugin bounds" % name)
    return result


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError("%s must be finite numeric" % label)
    return float(value)


def _render_sv(module: str, interface: LDOInterface, window: tuple[float, float], gain: float, tau: float, behavior: object) -> str:
    has_en = "EN" in interface.port_order
    ports = ["%s real %s" % (interface.port_map[name].direction, name) for name in ("VDD", "VSS")]
    if has_en:
        ports.append("input logic EN")
    ports.append("output real VOUT")
    low, high = window
    enable = "if (!EN) VOUT = VSS; else " if has_en else ""
    return (
        "// AIVW generated scaffold; deterministic gate required before promotion.\n"
        "// behavior_source=assumption_pending_spectre_correlation\n"
        "module %s (%s);\n"
        "  parameter real SUPPLY_MIN = %.12g;\n"
        "  parameter real SUPPLY_MAX = %.12g;\n"
        "  parameter real GAIN = %.12g;\n"
        "  parameter real TAU_SECONDS = %.12g;\n"
        "  always @* begin\n"
        "    %sif ((VDD < SUPPLY_MIN) || (VDD > SUPPLY_MAX)) VOUT = VSS;\n"
        "    else VOUT = VSS + GAIN * (VDD - VSS);\n"
        "  end\n"
        "endmodule\n"
    ) % (module, ", ".join(ports), low, high, gain, tau, enable)


def _render_smoke(module: str, interface: LDOInterface) -> str:
    has_en = "EN" in interface.port_order
    en_decl = "  logic en;\n" if has_en else ""
    en_conn = ", .EN(en)" if has_en else ""
    en_init = "    en = 1'b1;\n" if has_en else ""
    return (
        "module %s_smoke;\n"
        "  real vdd, vss, vout;\n%s"
        "  %s dut(.VDD(vdd), .VSS(vss)%s, .VOUT(vout));\n"
        "  initial begin vss = 0.0; vdd = 0.0;%s    #1; $finish; end\n"
        "endmodule\n"
    ) % (module, en_decl, module, en_conn, en_init)


__all__ = ["LinearRegulatorCandidate", "generate_linear_regulator_candidate"]
