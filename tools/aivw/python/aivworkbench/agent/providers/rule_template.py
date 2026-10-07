"""Deterministic template matching and bounded SV/RNM rendering.

The provider is a small rule engine, not a general template interpreter.  A
template contains literal port/topology predicates and numeric parameter
bounds.  Rendering uses named substitutions only; no Python, shell, Jinja, or
expression evaluation is performed at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import json
import math
import re
from typing import Any, Iterable, Mapping, Sequence

from ..backend import BaseProvider, ProviderRequest, ProviderResponse, ProviderUnavailable
from ...ldo import canonicalize_interface, canonicalize_structure, match_interface
from ..protocol import Action, ActionKind, AgentError, ErrorCode


MAX_RENDERED_CANDIDATE_BYTES = 512 * 1024


@dataclass(frozen=True)
class TemplateRule:
    template_id: str
    version: str
    ports: tuple[str, ...] = ()
    required_ports: tuple[str, ...] = ()
    topology: Mapping[str, Any] = field(default_factory=dict)
    parameters: Mapping[str, Any] = field(default_factory=dict)
    model_template: str = ""
    smoke_template: str = ""
    module_name: str = "aivw_candidate"
    lock_digest: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TemplateRule":
        allowed = {
            "id", "template_id", "version", "ports", "required_ports", "topology",
            "parameters", "model", "model_template", "smoke", "smoke_template",
            "module", "module_name", "lock_digest",
        }
        unknown = sorted(str(key) for key in value if not isinstance(key, str) or key not in allowed)
        if unknown:
            raise ValueError("template contains unknown fields: %s" % ", ".join(unknown))
        identifier = value.get("template_id", value.get("id"))
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError("template id is required")
        version = value.get("version", "1")
        if not isinstance(version, str) or not version.strip():
            raise ValueError("template version is required")
        ports = value.get("ports", ())
        required = value.get("required_ports", ports)
        if not isinstance(ports, (list, tuple)) or any(not isinstance(item, str) for item in ports):
            raise ValueError("template ports must be text array")
        if not isinstance(required, (list, tuple)) or any(not isinstance(item, str) for item in required):
            raise ValueError("template required_ports must be text array")
        topology = value.get("topology", {})
        parameters = value.get("parameters", {})
        if not isinstance(topology, Mapping) or not isinstance(parameters, Mapping):
            raise ValueError("template topology/parameters must be objects")
        model = value.get("model_template", value.get("model", ""))
        smoke = value.get("smoke_template", value.get("smoke", ""))
        if not isinstance(model, str) or not isinstance(smoke, str):
            raise ValueError("template model/smoke must be text")
        module = value.get("module_name", value.get("module", "aivw_candidate"))
        if not isinstance(module, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", module):
            raise ValueError("template module name is invalid")
        lock_digest = value.get("lock_digest")
        if lock_digest is not None and (not isinstance(lock_digest, str) or not re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", lock_digest)):
            raise ValueError("template lock digest is invalid")
        return cls(str(identifier), version, tuple(ports), tuple(required), dict(topology), dict(parameters), model, smoke, module, lock_digest)

    def digest(self) -> str:
        payload = {
            "template_id": self.template_id,
            "version": self.version,
            "ports": list(self.ports),
            "required_ports": list(self.required_ports),
            "topology": dict(self.topology),
            "parameters": dict(self.parameters),
            "model_template": self.model_template,
            "smoke_template": self.smoke_template,
            "module_name": self.module_name,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TemplateMatch:
    template_id: str
    version: str
    score: int
    matched_ports: tuple[str, ...]
    missing_ports: tuple[str, ...]
    parameter_values: Mapping[str, float]
    digest: str

    @property
    def matched(self) -> bool:
        return not self.missing_ports

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "version": self.version,
            "score": self.score,
            "matched_ports": list(self.matched_ports),
            "missing_ports": list(self.missing_ports),
            "parameter_values": dict(self.parameter_values),
            "template_digest": self.digest,
            "matched": self.matched,
        }


@dataclass(frozen=True)
class RenderedCandidate:
    source: str
    smoke_testbench: str
    template_id: str
    template_version: str
    template_digest: str
    parameters: Mapping[str, float]
    module_name: str
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            # Candidate text is intentionally carried in the run payload.  It
            # is bounded by the provider/runtime context cap at the next
            # boundary and is never written to a stable Virtuoso/template
            # location by this provider.
            "source": self.source,
            "smoke_testbench": self.smoke_testbench,
            "template_id": self.template_id,
            "template_version": self.template_version,
            "template_digest": self.template_digest,
            "parameters": dict(self.parameters),
            "module_name": self.module_name,
            "provenance": dict(self.provenance),
            "source_sha256": hashlib.sha256(self.source.encode("utf-8")).hexdigest(),
        }


class RuleTemplateProvider(BaseProvider):
    """Offline provider for a finite set of trusted templates."""

    name = "rule_template"
    version = "1"

    # M2's strict runtime profile requires every provider action to declare a
    # complete work budget.  The rule provider performs no model sampling or
    # simulation itself, but explicit zeroes are still important: an omitted
    # field must never be interpreted as an unbounded allowance.
    _ZERO_WORK = {
        "tokens": 0,
        "simulation_cases": 0,
        "simulation_seconds": 0,
    }

    @classmethod
    def _action_budget(cls, *, tool_calls: int = 0) -> dict[str, int | float]:
        value = dict(cls._ZERO_WORK)
        value["tool_calls"] = tool_calls
        return value

    def __init__(self, templates: Iterable[TemplateRule | Mapping[str, Any]], *, template_lock: str | None = None) -> None:
        super().__init__()
        self.templates = tuple(item if isinstance(item, TemplateRule) else TemplateRule.from_mapping(item) for item in templates)
        if not self.templates:
            raise ValueError("at least one template rule is required")
        self.template_lock = template_lock
        self.last_match: TemplateMatch | None = None
        self.last_candidate: RenderedCandidate | None = None

    @classmethod
    def for_ldo(cls, *, template_lock: str | None = None) -> "RuleTemplateProvider":
        rule = TemplateRule(
            template_id="ldo.linear_regulator",
            version="1.0.0",
            # The strict domain matcher selects the exact MASTER or AON
            # interface before rendering.  The template itself lists the
            # union only as a candidate vocabulary.
            ports=("VDD", "VSS", "EN", "VOUT"),
            required_ports=("VDD", "VSS", "VOUT"),
            topology={"kind": "linear_regulator"},
            parameters={
                "gain": {"default": 1.0, "min": 0.0, "max": 2.0},
                "tau": {"default": 1e-6, "min": 0.0, "max": 1.0},
            },
            module_name="ldo_candidate",
            model_template=(
                "module ${MODULE} (input real VDD, input real VSS, input logic EN, output real VOUT);\n"
                "  real gain; real tau;\n"
                "  initial begin gain = ${GAIN}; tau = ${TAU}; end\n"
                "  always @* begin\n"
                "    if (${HAS_EN} && !EN) VOUT = VSS;\n"
                "    else VOUT = VSS + gain * (VDD - VSS);\n"
                "  end\n"
                "endmodule\n"
            ),
            smoke_template=(
                "module ${MODULE}_smoke;\n"
                "  real vdd, vss, vout; logic en;\n"
                "  ${MODULE} dut(.VDD(vdd), .VSS(vss), .EN(en), .VOUT(vout));\n"
                "endmodule\n"
            ),
        )
        # The trusted built-in rule has a content-derived lock just like an
        # external template package.  Keep constructing providers with a
        # mismatched requested lock so ``render`` reports the stable
        # TEMPLATE_LOCK_MISMATCH error; an exact content lock is now a usable
        # path instead of being impossible because ``lock_digest`` was null.
        rule = replace(rule, lock_digest="sha256:" + rule.digest())
        return cls(
            [rule],
            template_lock=template_lock,
        )

    def match(self, context: Mapping[str, Any]) -> TemplateMatch | None:
        # The generic template matcher remains useful for legacy callers, but
        # LDO contexts are first checked by the strict domain matcher.  This
        # prevents the historical optional-EN scaffold from accepting an
        # extra port, wrong direction, or wrong order.
        if any(template.template_id == "ldo.linear_regulator" for template in self.templates):
            try:
                contract_value = context.get("interface_contract", context.get("interface"))
                structure_value = context.get("structure_ir", context.get("structure"))
                if isinstance(contract_value, Mapping) and isinstance(structure_value, Mapping):
                    structure_cell = structure_value.get("cell")
                    contract = canonicalize_interface(
                        contract_value,
                        cell=structure_cell if isinstance(structure_cell, str) else None,
                    )
                    structure = canonicalize_structure(structure_value, cell=contract.cell)
                    if not match_interface(contract, structure).matched:
                        self.last_match = None
                        return None
            except (ValueError, TypeError):
                self.last_match = None
                return None
        ports = _extract_ports(context)
        topology = _extract_topology(context)
        candidates: list[TemplateMatch] = []
        for template in self.templates:
            required = set(template.required_ports)
            matched = tuple(sorted(required.intersection(ports)))
            missing = tuple(sorted(required - ports))
            score = len(matched) * 10
            for key, expected in template.topology.items():
                if key in topology and topology[key] == expected:
                    score += 5
                elif key in topology:
                    score -= 5
            if missing:
                score -= len(missing) * 10
            values = _fit_parameters(template.parameters, context)
            candidates.append(TemplateMatch(template.template_id, template.version, score, matched, missing, values, template.digest()))
        candidates.sort(key=lambda item: (-item.score, item.template_id, item.version))
        selected = candidates[0] if candidates and candidates[0].matched else None
        self.last_match = selected
        return selected

    def render(self, context: Mapping[str, Any], *, match: TemplateMatch | None = None) -> RenderedCandidate:
        selected = match or self.match(context)
        if selected is None:
            raise ProviderUnavailable("TEMPLATE_UNMATCHED", "no deterministic template matched the supplied structure")
        template = next(item for item in self.templates if item.template_id == selected.template_id and item.version == selected.version)
        if self.template_lock is not None and template.lock_digest != self.template_lock:
            raise ProviderUnavailable(
                ErrorCode.TEMPLATE_LOCK_MISMATCH.value,
                "template does not carry the exact active lock digest",
            )
        context_ports = _extract_ports(context)
        # When a strict domain context is supplied, derive the terminal set
        # from the canonical structure rather than from a potentially stale
        # convenience ``ports`` field.
        structure_value = context.get("structure_ir", context.get("structure"))
        if isinstance(structure_value, Mapping):
            try:
                context_ports = set(canonicalize_structure(structure_value, strict=False).port_order)
            except (TypeError, ValueError):
                pass
        replacements = {
            "MODULE": template.module_name,
            "GAIN": _format_number(selected.parameter_values.get("gain", 1.0)),
            "TAU": _format_number(selected.parameter_values.get("tau", 0.0)),
            "HAS_EN": "1" if "EN" in context_ports else "0",
        }
        source_template = template.model_template
        smoke_template = template.smoke_template
        has_en = "EN" in context_ports
        if not has_en:
            # MASTER has no enable terminal.  Remove the complete declaration,
            # conditional behavior line, and smoke connection before
            # substitution.  In particular, remove the line containing the
            # ``${HAS_EN}`` placeholder itself; replacing first would leave a
            # dead ``!EN`` reference in otherwise valid MASTER source.
            source_lines = []
            for line in source_template.splitlines(keepends=True):
                if "${HAS_EN}" in line:
                    continue
                source_lines.append(line.replace(", input logic EN", ""))
            source_template = "".join(source_lines)
            # The optional branch line was paired with ``else`` in the
            # template.  Once the branch is removed, retain the transfer
            # statement itself without leaving a syntactically invalid bare
            # ``else`` in MASTER source.
            source_template = source_template.replace("    else VOUT", "    VOUT")
            smoke_template = smoke_template.replace("; logic en;", ";")
            smoke_template = smoke_template.replace(", .EN(en)", "")
        source = _substitute(source_template, replacements)
        smoke = _substitute(smoke_template, replacements)
        # Keep the topology boundary explicit even if a future template adds
        # another enable reference.  These checks are deliberately token based
        # and do not interpret arbitrary template expressions.
        if has_en:
            if not re.search(r"\bEN\b", source) or not re.search(r"\.EN\s*\(", smoke):
                raise ProviderUnavailable("TEMPLATE_TOPOLOGY_MISMATCH", "AON render must declare and connect EN")
        elif re.search(r"\bEN\b|\ben\b", source + "\n" + smoke):
            raise ProviderUnavailable("TEMPLATE_TOPOLOGY_MISMATCH", "MASTER render must not reference EN")
        if len(source.encode("utf-8")) > MAX_RENDERED_CANDIDATE_BYTES or len(smoke.encode("utf-8")) > MAX_RENDERED_CANDIDATE_BYTES:
            raise ProviderUnavailable(
                ErrorCode.CONTEXT_LIMIT_EXCEEDED.value,
                "rendered candidate exceeds the provider output cap",
            )
        candidate = RenderedCandidate(source, smoke, template.template_id, template.version, template.digest(), dict(selected.parameter_values), template.module_name, {"generation": "deterministic_rule", "match": selected.to_dict()})
        self.last_candidate = candidate
        return candidate

    def next_action(self, request: ProviderRequest) -> ProviderResponse:
        self._check_interrupt()
        if request.template_lock is not None and request.template_lock != self.template_lock:
            raise ProviderUnavailable(
                ErrorCode.TEMPLATE_LOCK_MISMATCH.value,
                "rule provider lock does not match the request lock",
            )
        context = request.context
        match = self.match(context)
        if match is None:
            action = Action(
                action_id="rule-blocked-%d" % len(str(context)),
                kind=ActionKind.BLOCKED.value,
                parent_event_id="%s-turn" % request.turn_id,
                expected_source_generation=request.source_generation,
                budget=self._action_budget(),
                params={"reason": "TEMPLATE_UNMATCHED", "template_candidates": [item.template_id for item in self.templates]},
                template_lock=request.template_lock,
            )
            return ProviderResponse.from_action(action, provider=self.name)
        # A proposal is emitted once per run.  The runtime feeds the tool
        # result back as ``previous_result``; without this guard a deterministic
        # provider would keep proposing the same candidate forever while the
        # state machine is already at STATIC_CHECK.
        previous = request.previous_result
        has_proposal = isinstance(previous, Mapping) and bool(previous.get("result"))
        gate = _find_gate_status(context)
        if has_proposal or gate in {"PASS", "QUALIFIED"}:
            kind = ActionKind.FINISH.value
            params = {"template_id": match.template_id, "template_version": match.version, "deterministic_gate_required": True}
        else:
            kind = ActionKind.PROPOSE_MODEL.value
            params = {"template_id": match.template_id, "template_version": match.version, "match": match.to_dict(), "candidate": self.render(context, match=match).to_dict()}
        action = Action(
            action_id="rule-%s" % request.turn_id.replace("/", "-"),
            kind=kind,
            parent_event_id="%s-turn" % request.turn_id,
            expected_source_generation=request.source_generation,
            budget=self._action_budget(
                tool_calls=0 if kind in (ActionKind.FINISH.value, ActionKind.BLOCKED.value) else 1
            ),
            params=params,
            idempotency_key="rule:%s:%s:%s" % (request.run_id, request.turn_id, kind),
            template_lock=request.template_lock,
        )
        return ProviderResponse.from_action(action, provider=self.name, model_id="rule-template/%s" % match.template_id)


def _extract_ports(context: Mapping[str, Any]) -> set[str]:
    for key in ("ports", "port_names", "interface_ports", "structure_ports"):
        value = context.get(key)
        if isinstance(value, Mapping):
            return {str(item) for item in value}
        if isinstance(value, (list, tuple, set)):
            return {str(item) for item in value}
    # Recipe contexts commonly carry contract/structure under distinct keys;
    # accept both the canonical interface object and a family manifest while
    # keeping the actual semantic match in ``match`` above.
    for key in ("interface", "interface_contract", "structure", "structure_ir"):
        interface = context.get(key)
        if not isinstance(interface, Mapping):
            continue
        variants = interface.get("variants")
        if isinstance(variants, Mapping):
            target = context.get("cell") or context.get("target_cell")
            if not isinstance(target, str):
                target_value = interface.get("target")
                target = target_value.get("cell") if isinstance(target_value, Mapping) else target_value
            if isinstance(target, str) and isinstance(variants.get(target), Mapping):
                interface = variants[target]
        value = interface.get("ports")
        if isinstance(value, Mapping):
            return {str(item) for item in value}
        if isinstance(value, (list, tuple)):
            return {str(item.get("name")) if isinstance(item, Mapping) else str(item) for item in value}
    return set()


def _extract_topology(context: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("topology", "structure", "structure_summary"):
        value = context.get(key)
        if isinstance(value, Mapping):
            return value
    return {}


def _fit_parameters(spec: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, float]:
    measurements = context.get("measurements", context.get("metrics", {}))
    measurements = measurements if isinstance(measurements, Mapping) else {}
    result: dict[str, float] = {}
    for name, declaration in spec.items():
        if isinstance(declaration, Mapping):
            default = declaration.get("default", 0.0)
            lower = declaration.get("min", default)
            upper = declaration.get("max", default)
        else:
            default, lower, upper = declaration, declaration, declaration
        value = measurements.get(name, default)
        try:
            number = float(value)
            low = float(lower)
            high = float(upper)
        except (TypeError, ValueError):
            number, low, high = float(default), float(lower), float(upper)
        if not math.isfinite(number):
            number = float(default)
        result[str(name)] = min(max(number, low), high)
    return result


def _find_gate_status(context: Mapping[str, Any]) -> str | None:
    for key in ("deterministic_gate", "gate_status", "status"):
        value = context.get(key)
        if isinstance(value, str):
            return value.upper()
    gates = context.get("gates")
    if isinstance(gates, Mapping):
        statuses = {str(item.get("status", "")).upper() for item in gates.values() if isinstance(item, Mapping)}
        if statuses and statuses.issubset({"PASS", "QUALIFIED"}):
            return "PASS"
    return None


def _substitute(template: str, replacements: Mapping[str, str]) -> str:
    if not isinstance(template, str):
        raise ValueError("template must be text")
    unknown: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in replacements:
            unknown.add(key)
            return ""
        return replacements[key]

    rendered = re.sub(r"\$\{([A-Za-z][A-Za-z0-9_]*)\}", replace, template)
    if unknown:
        raise ValueError("template contains unknown placeholder(s): %s" % ", ".join(sorted(unknown)))
    if "${" in rendered:
        raise ValueError("template contains an unresolved placeholder")
    return rendered


def _format_number(value: float) -> str:
    return "%.12g" % float(value)


# Compatibility alias used by early M0 notes.
RuleProvider = RuleTemplateProvider


__all__ = ["MAX_RENDERED_CANDIDATE_BYTES", "RenderedCandidate", "RuleProvider", "RuleTemplateProvider", "TemplateMatch", "TemplateRule"]
