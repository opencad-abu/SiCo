"""Resolve target electrical types from selected binding evidence, never PDK names."""

from .circuit_spec_schema import CircuitSpecError


def target_topology(spec, bindings):
    masters = {m["id"]: m for m in bindings["masters"]}
    devices = []
    for inst in spec["instances"]:
        master = masters.get(inst["master"])
        classification = master.get("classification") if master else None
        if not classification or classification["kind"] == "unknown":
            raise CircuitSpecError(
                "needs_binding: target device classification missing: " + inst["id"]
            )
        if not master["terminals_complete"] or inst["unconnected"]:
            raise CircuitSpecError("needs_binding: complete connected terminal inventory required")
        if set(inst["connections"]) != {t["name"] for t in master["terminals"]}:
            raise CircuitSpecError(
                "needs_binding: every selected master terminal must be connected"
            )
        devices.append({"id": inst["id"], "source_name": inst["name"],
                        "kind": classification["kind"], "role": master["kind"],
                        "attributes": classification["attributes"],
                        "pins": [
                            {"name": p, "net": n}
                            for p, n in sorted(inst["connections"].items())
                        ]})
    return {"level": "direct", "devices": devices, "gaps": [],
            "net_global_semantics": "uniform_boolean_v1",
            "nets": [{"id": n["name"], "source_name": n["name"], "num_bits": 1,
                      "is_global": n["scope"] != "local", "sig_type": "signal"}
                     for n in spec["nets"]],
            "ports": [{"id": "p" + str(i), "name": p["name"], "net": p["net"],
                       "num_bits": 1, "direction": p["direction"]}
                      for i, p in enumerate(spec["ports"])]}
