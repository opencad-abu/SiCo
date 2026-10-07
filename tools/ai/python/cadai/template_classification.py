"""Classify new captures from explicit evidence and observed installed builtin identity."""

from .circuit_spec_schema import CircuitSpecError, validate
from .template_classification_v1 import LEGACY_RULE_VERSION as V1_RULE_VERSION
from .template_classification_v1 import LEGACY_SEMANTICS, classify_v1
from .template_schema import RULE_VERSION as LEGACY_RULE_VERSION
from .template_schema import TARGET, TemplateError

SEMANTICS = "explicit_evidence_v2"
EXPLICIT_SEMANTICS = (LEGACY_SEMANTICS, SEMANTICS)
RULE_VERSION = "20260922.classification.v2"
CLASSIFICATIONS = TARGET["classifications"]


def graphic_role(instance):
    """Only observed installed, available symbol masters are builtin graphics."""
    if (not instance.get("master_available")
            or instance.get("master_view_type") != "schematicSymbol"):
        return None
    builtin, cell = instance.get("builtin_library"), instance["cellName"]
    if builtin == "basic":
        return {"ipin": "port_graphic", "opin": "port_graphic", "iopin": "port_graphic",
                "noConn": "no_connect", "gnd": "supply_marker", "vdd": "supply_marker"}.get(cell)
    if builtin == "analogLib" and cell in {"gnd", "vdd"}:
        return "supply_marker"
    return None


def classify(instance, pin_names, evidence=None):
    role = graphic_role(instance)
    if role:
        if evidence:
            raise TemplateError("classification cannot promote a builtin graphic")
        return role, role, {}
    # v1's electrical classification rules remain authoritative. Do not let its
    # wider graphic shortcut hide an unavailable or non-symbol master in v2.
    observed = instance
    if instance.get("builtin_library") == "basic" or (
            instance.get("builtin_library") == "analogLib"
            and instance["cellName"] in {"gnd", "vdd"}):
        observed = dict(instance, builtin_library=None)
    if instance.get("master_view_type") == "schematic":
        observed = dict(observed, schematic_available=True)
    return classify_v1(observed, pin_names, evidence)


def classification_rules(header, instances, classifications):
    semantics = header.get("classification_semantics")
    if semantics is None:
        if classifications:
            raise TemplateError("recapture with explicit classification semantics before evidence")
        from .template_classification_legacy import classify_legacy

        return classify_legacy, LEGACY_RULE_VERSION
    if semantics not in EXPLICIT_SEMANTICS:
        raise TemplateError("unsupported capture classification semantics")
    evidence = {} if classifications is None else classifications
    try:
        validate(evidence, CLASSIFICATIONS, "classifications")
    except CircuitSpecError as exc:
        raise TemplateError(str(exc)) from exc
    if set(evidence) - {instance["name"] for instance in instances}:
        raise TemplateError("classification refers to an absent source instance")
    def classify_observed(instance, pins):
        classifier = classify if semantics == SEMANTICS else classify_v1
        return classifier(instance, pins, evidence.get(instance["name"]))

    return classify_observed, RULE_VERSION if semantics == SEMANTICS else V1_RULE_VERSION
