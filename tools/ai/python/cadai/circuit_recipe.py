"""Versioned RC smoke recipe; no arbitrary SKILL, PDK callbacks or expressions."""
from __future__ import annotations

import hashlib
import json
import math

from .circuit_schema import PRESENTATION, string_schema, tool

NAME = string_schema(96, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
REF = string_schema(128, pattern=r"^[A-Za-z0-9:_-]+$")
REQUEST = {"request_id": string_schema(96, pattern=r"^[A-Za-z0-9_-]+$")}
SPEC = {"library": NAME, "cell": NAME,
        "resistance_ohm": {"type": "number", "minimum": 1, "maximum": 1e9, "default": 1000},
        "capacitance_f": {"type": "number", "minimum": 1e-15, "maximum": 1e-3, "default": 1e-9},
        "amplitude_v": {"type": "number", "minimum": 0.001, "maximum": 100, "default": 1.0}}
RECIPE_TOOLS = [
    tool("begin_circuit_task", "Record the user's foreground/background choice made at task start. "
         "Reuse task_id and task_ref for this task. Missing/unconfirmed/auto choices are rejected.",
         {"task_id": REQUEST["request_id"], "presentation": PRESENTATION,
          "choice_confirmed": {"type": "boolean"}}, ("task_id", "presentation", "choice_confirmed"), True),
    tool("preview_rc_circuit", "Validate RC low-pass testbench v1 and return topology, placement and "
         "analytic DC/TRAN expectations in SI units. Read-only; not live master qualification.",
         SPEC, ("library", "cell")),
    tool("create_rc_circuit", "Create NEW schematic from previewed RC recipe in an existing writable "
         "library. Validate live master pins/CDF, schCheck/connectivity, save; retain foreground window. "
         "Never overwrite. Retry only identical request_id and arguments to retrieve retained status.",
         {**SPEC, "task_ref": REF, **REQUEST, "preview_digest": string_schema(64, pattern="^[a-f0-9]{64}$")},
         ("library", "cell", "task_ref", "request_id", "preview_digest"), True),
    tool("create_rc_maestro", "Create NEW maestro view for this recipe's saved schematic, configure "
         "Spectre DC/TRAN and fixed scalar outputs, save and keep foreground Assembler open. Does not run.",
         {"circuit_ref": REF, **REQUEST}, ("circuit_ref", "request_id"), True),
    tool("run_rc_simulation", "Asynchronously run the retained RC Maestro setup with its existing site "
         "job policy. No automatic mode fallback. GUI stays open. Returns run_ref/history; no repeat launch.",
         {"circuit_ref": REF, **REQUEST}, ("circuit_ref", "request_id"), True),
    tool("get_rc_run_status", "Read the exact RC run's completion, error messages and retained history.",
         {"run_ref": REF}, ("run_ref",)),
    tool("read_rc_results", "Read scalar RDB outputs of this run's exact history/test/corner/point and "
         "compare to the recipe's analytic expectation; missing/error/multiple points fail qualification.",
         {"run_ref": REF}, ("run_ref",)),
    tool("stop_rc_simulation", "Request cancellation of this exact RC history. Never closes GUI windows. "
         "Poll status afterward; a stop request is not proof that simulator processes have exited.",
         {"run_ref": REF}, ("run_ref",), True),
    tool("release_rc_circuit", "Release the RC adapter after collecting results. Closes only its own "
         "unmodified idle background Maestro session; detaches from foreground windows without closing them.",
         {"circuit_ref": REF}, ("circuit_ref",), True),
]


def normalized_spec(arguments):
    from .circuit_tools import checked_arguments, CircuitArgumentError
    spec = checked_arguments("preview_rc_circuit", {k: v for k, v in arguments.items() if k in SPEC})
    for key in ("resistance_ohm", "capacitance_f", "amplitude_v"):
        spec[key] = float(spec[key])
    tau = spec["resistance_ohm"] * spec["capacitance_f"]
    if not 1e-12 <= tau <= 1:
        raise CircuitArgumentError("RC time constant must be between 1 ps and 1 s for recipe v1")
    return spec


def preview_rc_circuit(arguments):
    from .circuit_tools import checked_arguments
    spec = normalized_spec(checked_arguments("preview_rc_circuit", arguments))
    digest = hashlib.sha256(json.dumps({"recipe": "rc_lowpass_v1", **spec},
                                      sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    tau = spec["resistance_ohm"] * spec["capacitance_f"]
    # Pulse delay=tau, finite linear rise=tau/1000, measurement at delay+rise+tau.
    ratio = -math.expm1(-0.001) / 0.001
    expected = spec["amplitude_v"] * (1 - ratio * math.exp(-1))
    return {"ok": True, "recipe": "rc_lowpass_v1", "spec": spec, "preview_digest": digest,
            "target": [spec["library"], spec["cell"], "schematic"], "overwrite": False,
            "tau_s": tau, "pulse_delay_s": tau, "pulse_rise_s": tau / 1000,
            "sample_time_s": 2.001 * tau, "stop_time_s": 8 * tau,
            "expected": {"dc_out_v": spec["amplitude_v"], "tran_tau_v": expected},
            "absolute_tolerance_v": spec["amplitude_v"] * 0.002,
            "instances": [["V1", "analogLib/vpulse", [0, 1], "R0"],
                          ["R1", "analogLib/res", [1, 1], "R90"],
                          ["C1", "analogLib/cap", [2.5, 1], "R0"],
                          ["G0", "analogLib/gnd", [0, 0], "R0"]],
            "connections": {"IN": ["V1.PLUS", "R1.PLUS"],
                            "OUT": ["R1.MINUS", "C1.PLUS", "OUT.output"],
                            "gnd!": ["V1.MINUS", "C1.MINUS", "G0.gnd!"]},
            "notes": ["Self-contained driven testbench; output pin at right (3.5,1).",
                      "Live pin anchors are transformed from installed symbol masters.",
                      "DC source value equals amplitude; transient starts at zero.",
                      "This recipe does not qualify arbitrary PDK devices or generate a DUT symbol."]}


def build_recipe_skill(name, args):
    from .circuit_tools import CircuitArgumentError
    if name == "begin_circuit_task":
        if args["choice_confirmed"] is not True:
            raise CircuitArgumentError("ask the user for foreground/background at task start first")
        function, values = "aiCircuitBeginTask", [args["task_id"], args["presentation"], True]
    elif name == "create_rc_circuit":
        spec = normalized_spec(args)
        preview = preview_rc_circuit(spec)
        if preview["preview_digest"] != args["preview_digest"]:
            raise CircuitArgumentError("preview_digest differs; preview the exact target and parameters again")
        function = "aiRcCreate"
        values = [args["task_ref"], args["request_id"], spec["library"], spec["cell"],
                  spec["resistance_ohm"], spec["capacitance_f"], spec["amplitude_v"], args["preview_digest"]]
    else:
        function = {"create_rc_maestro": "aiRcMaestro", "run_rc_simulation": "aiRcRun",
                    "get_rc_run_status": "aiRcStatus", "read_rc_results": "aiRcResults",
                    "stop_rc_simulation": "aiRcStop", "release_rc_circuit": "aiRcRelease"}[name]
        values = [args.get("circuit_ref", args.get("run_ref"))]
        if "request_id" in args:
            values.append(args["request_id"])
    return function + "(" + " ".join("t" if v is True else json.dumps(v, ensure_ascii=False)
                                      for v in values) + ")"
