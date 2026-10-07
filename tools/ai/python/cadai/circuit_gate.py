"""Qualified gpdk045 v3.5 INV/NAND2 creation contract, not a general generator."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .circuit_recipe import NAME, REF, REQUEST
from .circuit_schema import string_schema, tool
from .circuit_gate_topology import gate_topology, testbench_topology

PROFILE = "gpdk045_v3_5_gates_v1"
SPEC = {"library": NAME, "cell": NAME, "gate": {"type": "string", "enum": ["INV", "NAND2"]},
        "gpdk_root": string_schema(1024)}
GATE_TOOLS = [
    tool("preview_gpdk_gate", "Preview the fixed gpdk045 v3.5 1V INV/NAND2 DUT, symbol and pulse "
         "testbench. Requires the PDK root containing gpdk045/ and models/. No OA writes or callbacks. "
         "Power pins upper-left, inputs lower-left, output right. Not simulation qualification.",
         SPEC, tuple(SPEC)),
    tool("create_gpdk_gate", "Create the previewed gate's NEW cells: cell/schematic, cell/symbol and "
         "cell_tb/schematic in an existing library. Verify qualified PDK file hashes, live CDF callbacks, "
         "connectivity, interfaces and saved readback. Uses confirmed task mode; never overwrites. "
         "Retry identical request_id only; failed partial cells are retained. Does not create Maestro or run.",
         {**SPEC, "task_ref": REF, **REQUEST,
          "preview_digest": string_schema(64, pattern="^[a-f0-9]{64}$")},
         (*SPEC, "task_ref", "request_id", "preview_digest"), True),
    tool("inspect_gpdk_gate", "Read and revalidate this gate's exact saved DUT/symbol/TB, CDF values, "
         "port directions and connections. Reject modified/closed/rebound foreground windows. "
         "Does not save, repair or change focus; no simulation-readiness claim.",
         {"circuit_ref": REF}, ("circuit_ref",)),
]


def preview_gpdk_gate(arguments):
    from .circuit_tools import checked_arguments, CircuitArgumentError
    spec = checked_arguments("preview_gpdk_gate", arguments)
    root = Path(spec["gpdk_root"])
    if not root.is_absolute():
        raise CircuitArgumentError("gpdk_root must be an absolute PDK root path")
    spec["gpdk_root"] = str(root.resolve())
    if spec["library"] in {"gpdk045", "basic", "analogLib"}:
        raise CircuitArgumentError("target must be a separate work library")
    payload = {"profile": PROFILE, "symbol_style": "pinbox_analog_annotate_v1",
               "spec": spec, "dut": gate_topology(spec["gate"]),
               "testbench": testbench_topology(spec["library"], spec["cell"], spec["gate"])}
    payload["preview_digest"] = hashlib.sha256(json.dumps(payload, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    return {"ok": True, **payload, "targets": [[spec["library"], spec["cell"], "schematic"],
        [spec["library"], spec["cell"], "symbol"], [spec["library"], spec["cell"]+"_tb", "schematic"]],
        "dimensions_nm": {"l": 45, "nmos_w": 480 if spec["gate"] == "NAND2" else 240, "pmos_w": 480},
        "supply_v": 1.0, "load_f": 1e-15, "simulation_qualified": False,
        "truth_table": [[0, 0, 1], [0, 1, 1], [1, 0, 1], [1, 1, 0]]
            if spec["gate"] == "NAND2" else [[0, 1], [1, 0]]}


def qualify_gpdk(root):
    from .circuit_tools import CircuitArgumentError
    manifest = Path(__file__).resolve().parents[2]/"reference/circuit-recipes/gpdk045_v3_5.json"
    try:
        hashes = json.loads(manifest.read_text())["sha256"]
        for relative, expected in hashes.items():
            if hashlib.sha256((Path(root)/relative).read_bytes()).hexdigest() != expected:
                raise CircuitArgumentError("unqualified gpdk045 profile file: " + relative)
    except (OSError, ValueError, KeyError) as exc:
        raise CircuitArgumentError("gpdk045 v3.5 profile unavailable: " + str(exc)) from exc


def skill_value(value):
    if isinstance(value, (list, tuple)):
        return "list(" + " ".join(skill_value(item) for item in value) + ")"
    return json.dumps(value, ensure_ascii=False)


def build_gate_skill(name, args):
    from .circuit_tools import CircuitArgumentError
    if name == "inspect_gpdk_gate":
        return "aiGateInspect(" + skill_value(args["circuit_ref"]) + ")"
    preview = preview_gpdk_gate({k: args[k] for k in SPEC})
    if args["preview_digest"] != preview["preview_digest"]:
        raise CircuitArgumentError("preview_digest differs; preview the exact gate and target again")
    spec = preview["spec"]
    qualify_gpdk(spec["gpdk_root"])
    dut, tb = preview["dut"], preview["testbench"]
    values = [args["task_ref"], args["request_id"], spec["library"], spec["cell"], spec["gate"],
              spec["gpdk_root"]+"/gpdk045", preview["preview_digest"],
              [dut["devices"], dut["ports"], dut["wires"]], dut["symbol_ports"],
              [tb["devices"], tb["ports"], tb["wires"]]]
    return "aiGateCreate(" + " ".join(skill_value(value) for value in values) + ")"
