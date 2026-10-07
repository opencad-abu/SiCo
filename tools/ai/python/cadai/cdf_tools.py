"""Explicit CDF inspection and new-target parameter probes via the current session."""

import hashlib
import json
import os
import uuid
from sicostate import project_directory

from .cdf_results import normalize
from .cdf_transport import transfer
from .circuit_create import skill_literal
from .circuit_schema import string_schema, tool
from .circuit_spec_schema import ID, TARGET, VALUE, CircuitSpecError, array, mapping, validate

REF = string_schema(128, pattern=r"^cdf:[a-f0-9]{32}$")
PROBE_REF = string_schema(128, pattern=r"^cdf-probe:[a-f0-9]{32}$")
REQUEST = string_schema(80, pattern=r"^[A-Za-z0-9_-]+$")
TASK = string_schema(128, pattern=r"^task:[A-Za-z0-9_-]+$")
PROBE_TARGET = {**TARGET, "properties": {
    **TARGET["properties"],
    "library": {"type": "string", "enum": ["SicoTest"]},
    "view": {"type": "string", "enum": ["schematic"]},
}}
CDF_TOOLS = [
    tool(
        "inspect_cdf",
        "Read effective cell CDF or a named direct instance's CDF without evaluating "
        "callbacks or editable/display expressions. Supply target and optional instance "
        "for instance CDF: target MUST be the containing schematic (not the device master "
        "symbol), and instance is its direct instance name. No editor window or PDK "
        "selection is required for this read. Omit instance to read the master cell CDF. "
        "for a fresh capture; supply only cdf_ref for frozen pages. Parameters show default, "
        "effective value and actual instance property separately. Does not discover PDKs. "
        "Unsaved instance values may be inspected; modified source masters cannot be probed.",
        {
            "target": TARGET,
            "instance": string_schema(128),
            "cdf_ref": REF,
            "parameters": array(ID, 64),
            "offset": {"type": "integer", "minimum": 0, "maximum": 512},
            "limit": {"type": "integer", "minimum": 1, "maximum": 64},
        },
        (),
    ),
    tool(
        "probe_cdf_parameters",
        "Create one diagnostic instance in an explicitly NEW SicoTest schematic. "
        "Create/reuse SicoTest in the current Virtuoso workspace automatically. Always use "
        "dbOpenCellViewByType in background, even for a foreground task; never open a probe window. "
        "Use a fresh effective-cell cdf_ref and existing task_ref; do not ask again for probe presentation. Hooks/callbacks "
        "use the built-in CDF executor; input cannot load code or choose "
        "arbitrary callbacks. Report requested vs effective/raw values, extension checkpoints "
        "when instrumented, and source-CDF restoration. Save/readback the diagnostic view, "
        "but do not schCheck unconnected test pins or claim circuit/simulation qualification. "
        "Retain request_id after unknown status; failures retain partial targets. "
        "Returns bounded synchronous SKILL output, warnings and errors in output, "
        "with output_truncated/output_capture; retained with the probe outcome. "
        "Supply callback_order listing the changed parameters once each. GUI editable/display "
        "conditions are not evaluated. Optional installed project overrides remain supported. No automatic cache reuse.",
        {
            "cdf_ref": REF,
            "task_ref": TASK,
            "request_id": REQUEST,
            "target": PROBE_TARGET,
            "parameters": mapping(VALUE),
            "extension_ref": string_schema(256),
            "callback_order": array(ID, 16, 1),
        },
        ("cdf_ref", "task_ref", "request_id", "target", "parameters"),
        mutating=True,
    ),
    tool(
        "get_cdf_probe",
        "Retrieve the immutable diagnostic outcome for a probe_ref in this Virtuoso session. "
        "Includes the original probe's captured output, including failed probes. "
        "Does not rerun callbacks or certify that the saved view is still unchanged. "
        "Use inspect_cdf on that view and instance I_CDF_PROBE_0 for a fresh inspection.",
        {"probe_ref": PROBE_REF},
        ("probe_ref",),
    ),
]
CDF_NAMES = frozenset(t["name"] for t in CDF_TOOLS)


def build_call(name, args):
    schema = next(t["inputSchema"] for t in CDF_TOOLS if t["name"] == name)
    validate(args, schema)
    if name == "inspect_cdf":
        if ("target" in args) == ("cdf_ref" in args) or (
            "instance" in args and "target" not in args
        ):
            raise CircuitSpecError("supply target [+ instance] or cdf_ref, exclusively")
        if args.get("parameters") and len(set(args["parameters"])) != len(args["parameters"]):
            raise CircuitSpecError("duplicate parameter filters")
        function = "aiCdfInspect"
        values = [
            (
                [args["target"][k] for k in ("library", "cell", "view")]
                if "target" in args
                else None
            ),
            args.get("instance"),
            args.get("cdf_ref"),
            args.get("parameters"),
            args.get("offset", 0),
            args.get("limit", 32),
            "cdf:" + uuid.uuid4().hex,
        ]
    elif name == "probe_cdf_parameters":
        if args["target"]["view"] != "schematic":
            raise CircuitSpecError("probe target view must be schematic")
        extension = args.get("extension_ref")
        order = args.get("callback_order")
        if order is not None:
            if len(order) != len(set(order)) or set(order) != set(args["parameters"]):
                raise CircuitSpecError("callback_order must list each changed parameter exactly once")
            extension = extension or "builtin:cdf-callbacks:v1"
        elif extension == "builtin:cdf-callbacks:v1":
            raise CircuitSpecError("built-in callback execution requires callback_order")
        rows = []
        for key in order if order is not None else sorted(args["parameters"]):
            value = args["parameters"][key]
            typ = (
                "boolean"
                if isinstance(value, bool)
                else (
                    "string"
                    if isinstance(value, str)
                    else "integer"
                    if isinstance(value, int)
                    else "number"
                )
            )
            rows.append([key, typ, value])
        function = "aiCdfProbe"
        values = [
            args["cdf_ref"],
            args["task_ref"],
            args["request_id"],
            [args["target"][k] for k in ("library", "cell", "view")],
            rows,
            extension,
            "cdf-probe:" + uuid.uuid4().hex,
        ]
    else:
        function, values = "aiCdfProbeGet", [args["probe_ref"]]
    code = function + "(" + " ".join(skill_literal(v) for v in values) + ")"
    if len(code.encode()) > 49152:
        raise CircuitSpecError("CDF request exceeds 48 KiB")
    return code


def call_cdf(name, args, client, workspace):
    native_ok, reply = transfer(client, build_call(name, args), capture=name == "inspect_cdf")
    if not native_ok and not reply.get("probe_ref"):
        return False, reply
    result = normalize(reply, name)
    content = json.dumps(
        result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
    ).encode()
    if len(content) > 4 * 1024 * 1024:
        raise CircuitSpecError("CDF evidence exceeds 4 MiB")
    sha = hashlib.sha256(content).hexdigest()
    directory = project_directory(workspace, "ai/cdf-probes", create=True)
    path = directory / (sha + ".json")
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
    except FileExistsError:
        if path.read_bytes() != content:
            raise CircuitSpecError("CDF evidence file content conflict")
    result["artifact"] = {"path": str(path), "sha256": sha, "bytes": len(content)}
    if name != "inspect_cdf":
        # Keep the full bounded diagnostic data in evidence, report useful differences inline.
        result.pop("parameters", None)
        result.pop("raw_properties", None)
        result["baseline_parameters"] = "see artifact"
        for step in result.get("trace", []):
            step.pop("values", None)

    def inline_size():
        return len(json.dumps(json.dumps(result, ensure_ascii=False), ensure_ascii=False).encode())

    if inline_size() > 800000 and name != "inspect_cdf":
        # Complete details remain in the hash-checked artifact; truncation is explicit.
        result["trace"] = [
            dict(label=s["label"], change_count=len(s["changes"])) for s in result.get("trace", [])
        ]
        result["trace_inline_complete"] = False
    if inline_size() > 800000:
        result["inline_complete"] = False
        for field in (
            "parameters",
            "sim_info",
            "simulators",
            "hooks",
            "requested_matches",
            "trace",
        ):
            if field in result:
                result[field] = {"status": "artifact_only", "reason": "inline_response_limit"}
    return bool(reply.get("ok")), result
