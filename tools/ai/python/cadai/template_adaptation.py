"""Create and independently revalidate immutable use v2 electrical evidence."""

import copy

from .circuit_spec_plan import preview_circuit
from .circuit_spec_schema import CircuitSpecError, digest, validate
from .template_adapt_schema import MAP, REQUEST, USE, USE_VERSION, VERIFIER_VERSION
from .template_embedding import verify_embedding
from .template_omit_selection import omit_selected
from .template_reuse_schema import validate_record
from .template_rules import authorize
from .template_schema import TemplateError
from .template_source_qualification import require_qualified
from .template_target import target_topology

INVARIANTS = [
    "complete_target", "typed_devices", "terminal_inventory", "protected_connections",
    "injective_maps", "net_scope", "port_identity", "declared_boundaries",
    "residual_preserved",
]


def adapt(record, spec, bindings, mapping, allowed_rules):
    try:
        validate_record(record)
        require_qualified(record)
        validate(mapping, MAP, "mapping")
        validate(allowed_rules, REQUEST["properties"]["allowed_rule_refs"], "allowed_rule_refs")
        # The proof always binds the complete canonical request, even when this
        # pure entry point is used directly rather than through the public tool.
        spec = preview_circuit(spec, bindings)["plan"]["spec"]
        source, removed = omit_selected(record, mapping["omitted_groups"])
        target = target_topology(spec, bindings)
        checked = verify_embedding(record, source, target, mapping)
        residual = checked["residual"]
        if residual and mapping["omitted_groups"]:
            raise TemplateError(
                "needs_adaptation: omission cannot mix with a residual boundary group")
        renamed_nets = {n["id"]: mapping["net_map"][n["id"]] for n in source["nets"]
                        if n["source_name"] != mapping["net_map"][n["id"]]}
        renamed_ports = {p: n for p, n in mapping["port_map"].items() if p != n}
        diff = {"removed_" + k: v for k, v in removed.items()}
        diff.update(added_instances=residual, added_nets=checked["extra_nets"],
                    added_ports=checked["extra_ports"], renamed_nets=renamed_nets,
                    renamed_ports=renamed_ports)
        rules = []
        if renamed_nets or renamed_ports:
            rules.append(("rename", sorted(renamed_nets) + sorted(renamed_ports)))
        if mapping["omitted_groups"]:
            rules.extend(("omit_optional_group", [group]) for group in mapping["omitted_groups"])
        if residual:
            rules.append(("add_boundary_group", residual))
        adaptations, refs = [], {}
        for name, site in rules:
            ref = authorize(name, allowed_rules, record["reuse_contract"]["allowed_rule_refs"])
            refs[name] = ref
            adaptations.append({"rule_ref": ref, "site": site,
                                "input_graph_digest": digest(record["topology"]),
                                "output_graph_digest": digest(target), "diff": diff})
        use = {"schema": USE_VERSION, "template_ref": record["template_ref"],
               "target_spec_digest": digest(spec),
               **{k: copy.deepcopy(mapping[k]) for k in MAP["properties"] if k != "omitted_groups"},
               "boundary_map": [{**link, "rule_ref": refs["add_boundary_group"]}
                                for link in checked["links"]],
               "residual_instances": residual, "adaptations": adaptations,
               "verification": {"version": VERIFIER_VERSION, "record_digest": digest(record),
                                "checked_invariants": INVARIANTS, "gaps": []}}
        validate(use, USE, "template_use")
        return copy.deepcopy(use)
    except TemplateError as exc:
        raise CircuitSpecError(str(exc)) from exc


def verify_use(record, spec, use, bindings):
    validate(use, USE, "template_use")
    normalized = preview_circuit(spec, bindings)["plan"]["spec"]
    if use["target_spec_digest"] != digest(normalized):
        raise CircuitSpecError("target_spec_digest changed; prepare the exact target again")
    omissions = [
        a["site"] for a in use["adaptations"]
        if a["rule_ref"]["id"] == "omit_optional_group"
    ]
    mapping = {k: use[k] for k in MAP["properties"] if k != "omitted_groups"}
    mapping["omitted_groups"] = [group for site in omissions for group in site]
    refs = []
    for adaptation in use["adaptations"]:
        if adaptation["rule_ref"] not in refs:
            refs.append(adaptation["rule_ref"])
    actual = adapt(record, normalized, bindings, mapping, refs)
    if actual != use:
        raise CircuitSpecError(
            "template_use evidence differs from independently verified adaptation"
        )
    return actual
