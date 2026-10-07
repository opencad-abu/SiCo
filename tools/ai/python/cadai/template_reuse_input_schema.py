"""Source-name extraction intent; no internal graph, rule digest or deletion inventory."""

from .circuit_spec_schema import array, enum, obj


def reuse_input(name):
    names = {**array(name, 64, 1), "uniqueItems": True}
    count = {"type": "integer", "minimum": 0, "maximum": 4096}
    return obj({
        "mode": enum("whole", "core"),
        "allow_rename": {"type": "boolean"},
        "core_instances": names,
        "optional_groups": array(obj({"id": name, "instances": names}), 64),
        "boundary_terminals": array(obj({
            "endpoint": obj({"instance": name, "terminal": name}),
            "allowed_device_kinds": names,
            "min_additional_connections": count,
            "max_additional_connections": count,
        }), 256),
        "allow_omit_optional_groups": {"type": "boolean"},
        "allow_add_boundary_group": {"type": "boolean"},
    }, ("mode", "allow_rename"))


def validate_intent(options):
    from .template_schema import TemplateError, canonical

    core_fields = {"core_instances", "optional_groups", "boundary_terminals",
                   "allow_omit_optional_groups", "allow_add_boundary_group"}
    if options["mode"] == "whole" and core_fields & options.keys():
        raise TemplateError("reuse.mode=whole cannot declare core/optional/boundary fields")
    if options["mode"] == "core" and core_fields - options.keys():
        raise TemplateError("reuse.mode=core requires " + ", ".join(sorted(
            core_fields - options.keys())))
    if len(canonical(options).encode("utf-8")) > 24000:
        raise TemplateError("reuse input exceeds 24000 bytes; reduce the contract request")
