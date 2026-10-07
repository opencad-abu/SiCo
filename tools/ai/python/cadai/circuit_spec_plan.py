"""PDK-independent topology checks and symbolic layout plans. Never accesses OA."""

from __future__ import annotations

import copy
import re
from pathlib import PurePosixPath

from .circuit_spec_schema import (
    BINDINGS,
    MAX_INPUT_BYTES,
    PLAN_VERSION,
    SPEC,
    CircuitSpecError,
    canonical,
    digest,
    unique,
    validate,
)


def _validate_bindings(spec, bindings):
    validate(bindings, BINDINGS, "bindings")
    if (
        bindings["project_ref"] != spec["project_ref"]
        or bindings["snapshot_ref"] != spec["binding_snapshot"]
    ):
        raise CircuitSpecError("project_ref/binding_snapshot differ from supplied bindings")
    masters = unique(bindings["masters"], "id", "bindings.masters")
    library_paths, targets = {}, {}
    for master in masters.values():
        if not PurePosixPath(master["library_path"]).is_absolute():
            raise CircuitSpecError("master library_path must be an absolute source path")
        library = master["target"]["library"]
        path = str(PurePosixPath(master["library_path"]))
        if library_paths.setdefault(library, path) != path:
            raise CircuitSpecError(
                "same library name has conflicting paths in this session snapshot"
            )
        target = tuple(master["target"][k] for k in ("library", "cell", "view"))
        identity = {k: v for k, v in master.items() if k != "id"}
        if targets.setdefault(target, identity) != identity:
            raise CircuitSpecError("same master has conflicting metadata in this snapshot")
        unique(master["terminals"], "name", "master terminals")
        unique(master["parameters"], "name", "master parameters")
        if master["callbacks"]["status"] != "required" and "extension_ref" in master["callbacks"]:
            raise CircuitSpecError("extension_ref requires callback status required")
        for parameter in master["parameters"]:
            for choice in parameter.get("choices", []):
                _parameter_type(parameter, choice)
    return masters


def _parameter_type(parameter, value):
    validate(
        value, {"type": parameter["type"], "maxLength": 1024}, "parameter." + parameter["name"]
    )


def _names(instances, masters):
    # Reserve explicit names first, so automatic naming never displaces a user choice.
    used = set()
    for instance in instances:
        name = instance.get("name")
        if name is not None:
            if name in used:
                raise CircuitSpecError("duplicate instance name " + name)
            used.add(name)
    result = {}
    for instance in sorted(instances, key=lambda row: row["id"]):
        master = masters.get(instance["master"])
        name = instance.get("name")
        if master and master["kind"] == "design":
            cell = master["target"]["cell"]
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", cell):
                raise CircuitSpecError("design cell cannot form I_cellName_indexNumber")
            prefix = "I_" + cell + "_"
            if name and not re.fullmatch(re.escape(prefix) + r"[0-9]+", name):
                raise CircuitSpecError("design instance name must be I_cellName_indexNumber")
            if name is None:
                index = 0
                while prefix + str(index) in used:
                    index += 1
                name = prefix + str(index)
                if len(name) > 96:
                    raise CircuitSpecError("generated design instance name exceeds 96 characters")
                used.add(name)
        if not name and master is not None:
            raise CircuitSpecError("explicit instance name required for device " + instance["id"])
        result[instance["id"]] = name
    return result


def _binding_gaps(instance, master, issues):
    def missing(code, **details):
        issues.append(
            {"code": code, "instance": instance["id"], "master": instance["master"], **details}
        )

    if master is None:
        missing("master_binding_missing")
        if not instance.get("name"):
            missing("instance_name_unresolved")
        return
    assigned = set(instance["connections"]) | set(instance["unconnected"])
    terminals = {row["name"] for row in master["terminals"]}
    if master["terminals_complete"]:
        if assigned - terminals:
            raise CircuitSpecError(
                instance["id"] + ": unknown terminals " + ", ".join(sorted(assigned - terminals))
            )
        for terminal in sorted(terminals - assigned):
            missing("terminal_unassigned", terminal=terminal)
    else:
        missing("terminal_metadata_incomplete")
    params = {row["name"]: row for row in master["parameters"]}
    if not master["parameters_complete"]:
        missing("parameter_metadata_incomplete")
    for name, value in instance["parameters"].items():
        if name not in params:
            if master["parameters_complete"]:
                raise CircuitSpecError(instance["id"] + ": unknown parameter " + name)
            missing("parameter_metadata_missing", parameter=name)
            continue
        param = params[name]
        if not param["editable"]:
            raise CircuitSpecError(instance["id"] + ": parameter is not editable: " + name)
        _parameter_type(param, value)
        if "choices" in param and value not in param["choices"]:
            raise CircuitSpecError(instance["id"] + ": parameter choice not allowed: " + name)
    callbacks = master["callbacks"]
    if callbacks["status"] == "unknown":
        missing("callback_policy_unknown")
    elif callbacks["status"] == "required":
        # A supplied extension reference is evidence to hand off, never permission to execute.
        missing("callback_extension_not_validated", extension_ref=callbacks.get("extension_ref"))


def _placement(spec, names):
    columns = {
        "stimulus": "left",
        "dut": "center",
        "load": "right",
        "auxiliary": "bottom",
        "device": "center",
    }
    counts = {}
    instances = []
    for instance in sorted(spec["instances"], key=lambda row: row["id"]):
        column = columns[instance["role"]] if spec["kind"] == "testbench" else "template_or_user"
        order = counts.get(column, 0)
        counts[column] = order + 1
        instances.append(
            {"id": instance["id"], "name": names[instance["id"]], "column": column, "order": order}
        )
    ports = [
        {
            "name": p["name"],
            "region": {"inputOutput": "upper_left", "input": "lower_left", "output": "right"}[
                p["direction"]
            ],
        }
        for p in sorted(spec["ports"], key=lambda p: p["name"])
    ]
    stubs = [
        {
            "endpoint": {"instance": i["id"], "terminal": terminal},
            "net": net,
            "anchor": "resolve_target_master",
            "label": net,
        }
        for i in sorted(spec["instances"], key=lambda row: row["id"])
        for terminal, net in sorted(i["connections"].items())
    ]
    stubs += [
        {
            "endpoint": {"port": p["name"]},
            "net": p["net"],
            "label": p["net"],
            "anchor": "resolve_port_placement",
        }
        for p in sorted(spec["ports"], key=lambda row: row["name"])
    ]
    return {
        "coordinate_status": "deferred_until_live_geometry",
        "instances": instances,
        "ports": ports,
        "connection_style": "labelled_stubs",
        "stubs": stubs,
        "geometry_verified": False,
        "notes": spec.get("notes", []),
    }


def preview_circuit(spec, bindings):
    """A structurally valid plan is not a live PDK, geometry, or simulation qualification."""
    if len(canonical({"spec": spec, "bindings": bindings}).encode("utf-8")) > MAX_INPUT_BYTES:
        raise CircuitSpecError("circuit preview input exceeds 192 KiB; split the design")
    validate(spec, SPEC, "spec")
    spec, bindings = copy.deepcopy(spec), copy.deepcopy(bindings)
    masters = _validate_bindings(spec, bindings)
    instances = unique(spec["instances"], "id", "spec.instances")
    nets = unique(spec["nets"], "name", "spec.nets")
    unique(spec["ports"], "name", "spec.ports")
    if spec["kind"] == "testbench":
        if not any(i["role"] == "dut" for i in instances.values()):
            raise CircuitSpecError("testbench requires at least one explicitly identified DUT")
        if any(i["role"] == "device" for i in instances.values()):
            raise CircuitSpecError("testbench instances require dut/stimulus/load/auxiliary roles")
        for instance in instances.values():
            master = masters.get(instance["master"])
            if instance["role"] == "dut" and master is not None:
                if master["kind"] != "design" or master["target"]["view"] != "symbol":
                    raise CircuitSpecError(
                        "testbench DUT requires a design symbol view; inspect the design unit, "
                        "create and verify its missing symbol before TB creation; "
                        "never copy or flatten its schematic into the TB"
                    )
    elif any(i["role"] != "device" for i in instances.values()):
        raise CircuitSpecError("circuit instances use device role; TB roles belong to testbench")
    for net in nets.values():
        if (net["scope"] == "global") != net["name"].endswith("!"):
            raise CircuitSpecError("v1 global scope requires an explicit trailing ! marker")
        if (
            net["scope"] == "global"
            and spec["kind"] == "testbench"
            and not spec.get("global_net_authorization")
        ):
            raise CircuitSpecError(
                "TB global nets require explicit user authorization; "
                "use basic/gnd with physical pin links for ground"
            )
        if net["scope"] == "ground" and spec["kind"] != "testbench":
            raise CircuitSpecError("explicit basic/gnd ground scope is currently for testbenches")
    if sum(n["scope"] == "ground" for n in nets.values()) > 1:
        raise CircuitSpecError("basic/gnd supplies one electrical ground; declare one ground net")
    if any(n["scope"] == "ground" for n in nets.values()) and "gnd!" in nets:
        raise CircuitSpecError("ground scope cannot be mixed with explicit gnd! net")
    names = _names(spec["instances"], masters)
    issues, warnings = [], []
    endpoints = {net: [] for net in nets}
    for instance in instances.values():
        if set(instance["connections"]) & set(instance["unconnected"]):
            raise CircuitSpecError("terminal cannot be both connected and explicitly unconnected")
        for terminal, net in instance["connections"].items():
            if net not in nets:
                raise CircuitSpecError(instance["id"] + ": undeclared net " + net)
            endpoints[net].append({"instance": instance["id"], "terminal": terminal})
        for terminal, reason in instance["unconnected"].items():
            warnings.append(
                {
                    "code": "intentional_unconnected",
                    "instance": instance["id"],
                    "terminal": terminal,
                    "reason": reason,
                }
            )
        _binding_gaps(instance, masters.get(instance["master"]), issues)
    for port in spec["ports"]:
        if port["net"] not in nets:
            raise CircuitSpecError("port references undeclared net " + port["net"])
        if nets[port["net"]]["scope"] == "ground":
            raise CircuitSpecError("TB ground uses basic/gnd pin links, not a ground port")
        if port["name"] != port["net"]:
            raise CircuitSpecError(
                "v1 port name must equal its net name; alias semantics are not supported"
            )
        endpoints[port["net"]].append({"port": port["name"]})
    for net, rows in endpoints.items():
        if not rows:
            raise CircuitSpecError("declared net has no endpoints: " + net)
        if len(rows) == 1:
            warnings.append({"code": "single_endpoint_net", "net": net})
    for note in spec.get("notes", []):
        if set(note["instances"]) - set(instances) or len(set(note["instances"])) != len(
            note["instances"]
        ):
            raise CircuitSpecError("note references unknown/duplicate instances")
    normal = {
        **spec,
        "instances": sorted(spec["instances"], key=lambda r: r["id"]),
        "nets": sorted(spec["nets"], key=lambda r: r["name"]),
        "ports": sorted(spec["ports"], key=lambda r: r["name"]),
        "notes": spec.get("notes", []),
    }
    for instance in normal["instances"]:
        if names[instance["id"]] is not None:
            instance["name"] = names[instance["id"]]
    plan = {
        "schema": PLAN_VERSION,
        "spec": normal,
        "bindings": bindings,
        "binding_digest": digest(bindings),
        "binding_evidence_kind": bindings["evidence_kind"],
        "binding_source_verified": False,
        "connectivity": [
            {**nets[net], "endpoints": sorted(rows, key=canonical)}
            for net, rows in sorted(endpoints.items())
        ],
        "placement": _placement(normal, names),
        "issues": sorted(issues, key=canonical),
        "warnings": sorted(warnings, key=canonical),
        "structural_valid": True,
        "binding_complete": not issues,
        "live_binding_verified": False,
        "creation_ready": False,
        "simulation_qualified": False,
        "next_requirements": [
            "live_binding_revalidation",
            "target_geometry_and_routing",
            "creation_backend",
        ],
    }
    geometry_ready = all(issue["code"] == "callback_extension_not_validated"
                         and issue.get("extension_ref") for issue in issues)
    return {
        "ok": True,
        "stage": "preview",
        "status": "binding_incomplete" if issues else "structural_preview",
        "geometry_preview_ready": geometry_ready,
        "next_action": "preview_circuit_geometry" if geometry_ready else "resolve_device_bindings",
        "user_input_required": False,
        "preview_digest": digest(plan),
        "plan": plan,
    }
