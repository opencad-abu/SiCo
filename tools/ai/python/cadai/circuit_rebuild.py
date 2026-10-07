"""Bounded reference OpAmp capture and new DUT/symbol/testbench reconstruction."""
from pathlib import Path

from .circuit_gate import qualify_gpdk, skill_value
from .circuit_recipe import NAME, REF, REQUEST
from .circuit_schema import string_schema, tool

ROOT = {"gpdk_root": string_schema(1024)}
REBUILD_TOOLS = [
    tool("preview_opamp_rebuild", "Read the paired gpdk045 v3.5 OpAmp reference through OA APIs; "
         "capture source generation and target plan. No design writes. Requires confirmed task "
         "and separate existing work library.",
         {"library": NAME, "cell": NAME, "task_ref": REF, **REQUEST, **ROOT},
         ("library", "cell", "task_ref", "request_id", "gpdk_root")),
    tool("create_opamp_dut", "Create NEW OpAmp DUT schematic/symbol and AC/TRAN testbenches from "
         "the captured preview. Preserve reference stored/effective CDF; schCheck, interfaces and "
         "saved connections. AC feedback helper remains read-only source dependency. Does not run.",
         {"preview_ref": REF, **REQUEST, **ROOT}, ("preview_ref", "request_id", "gpdk_root"), True),
    tool("inspect_opamp_dut", "Revalidate exact saved DUT/symbol/TBs, reference generation and "
         "foreground window identities. Does not check/save/repair user edits.",
         {"circuit_ref": REF}, ("circuit_ref",)),
    tool("create_opamp_rebuild_maestro", "Create new AC/TRAN configs and Maestro in <cell>_sim, "
         "binding the saved rebuilt testbenches with maeSetDesign. Preserve reference analyses/"
         "measurements; validate designs/configs on every run. Does not run or overwrite.",
         {"circuit_ref": REF, **REQUEST, **ROOT}, ("circuit_ref", "request_id", "gpdk_root"), True),
    tool("run_opamp_rebuild", "Start one asynchronous nominal AC+TRAN run of a validated rebuilt "
         "OpAmp setup. Retain exact history; use get_opamp_reference_status/read_opamp_reference_results/"
         "stop_opamp_reference/release_opamp_reference for lifecycle and nine scalars. Does not qualify specs.",
         {"setup_ref": REF, **REQUEST, **ROOT}, ("setup_ref", "request_id", "gpdk_root"), True),
]
REBUILD_NAMES = frozenset(t["name"] for t in REBUILD_TOOLS)


def build_rebuild_skill(name, args):
    from .circuit_tools import CircuitArgumentError
    if "gpdk_root" in args:
        root = Path(args["gpdk_root"])
        if not root.is_absolute():
            raise CircuitArgumentError("gpdk_root must be absolute")
        root = root.resolve()
        qualify_gpdk(root)
    if name == "preview_opamp_rebuild":
        if args["library"] in {"Two_Stage_Opamp", "gpdk045", "basic", "analogLib"}:
            raise CircuitArgumentError("separate work library required")
        source = root.parent / "libs/Two_Stage_Opamp"
        if not (source / "OpAmp/schematic").is_dir():
            raise CircuitArgumentError("paired reference unavailable")
        fn, vals = "aiRebuildPreview", [args["task_ref"], args["request_id"], args["library"],
                                       args["cell"], str(root), str(source)]
    elif name == "create_opamp_dut":
        fn, vals = "aiRebuildCreate", [args["preview_ref"], args["request_id"], str(root)]
    elif name == "create_opamp_rebuild_maestro":
        fn, vals = "aiRebuildMaestro", [args["circuit_ref"], args["request_id"], str(root)]
    elif name == "run_opamp_rebuild":
        fn, vals = "aiRebuildRun", [args["setup_ref"], args["request_id"], str(root)]
    else:
        fn, vals = "aiRebuildInspect", [args["circuit_ref"]]
    return fn + "(" + " ".join(skill_value(v) for v in vals) + ")"
