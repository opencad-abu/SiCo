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
        "Read effective cell CDF or a direct instance's CDF without evaluating "
        "callbacks or edit/display expressions. For instance CDF supply target (the "
        "containing schematic, not the master symbol) and the direct instance name; "
        "omit instance for the master CDF. "
        "Live reads need a fresh ref; frozen pages take cdf_ref only. Parameters show "
        "default, effective value and instance property separately. Unsaved instance "
        "values may be inspected; modified masters cannot be probed.",
        {
            "target": TARGET,
            "instance": string_schema(128),
            "cdf_ref": REF,
            "parameters": array(ID, 64),
            "offset": {"type": "integer", "minimum": 0, "maximum": 512},
            "limit": {"type": "integer", "minimum": 1, "maximum": 64},
        },
        (),
        help_text="This read does not discover PDKs.",
    ),
    tool(
        "probe_cdf_parameters",
        "Create one diagnostic instance in an explicitly NEW SicoTest schematic (reuse the "
        "workspace SicoTest). Always use dbOpenCellViewByType in the background, even for a "
        "foreground task; never open a probe window. Use a fresh effective-cell cdf_ref and the "
        "existing task_ref; do not ask again for probe presentation. Hooks/callbacks use the "
        "built-in CDF executor; input cannot load code or choose arbitrary callbacks. Report "
        "requested vs effective/raw values, extension checkpoints when instrumented, and "
        "source-CDF restoration. Save and read back the diagnostic view, but do not schCheck "
        "unconnected test pins or claim circuit/simulation qualification. Retain request_id after "
        "unknown status; failures keep partial targets.",
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
        help_text=(
            "callback_order lists changed parameters once each and GUI editable/display conditions "
            "are not evaluated. Output is bounded synchronous "
            "SKILL text (warnings and errors included) with "
            "output_truncated/output_capture markers, retained with the probe outcome. Optional "
            "installed project overrides remain supported. No automatic cache reuse."),
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

# 单个模拟器接口表在内联结果里的上限；更大的表以名称索引呈现，完整表留在 artifact。
SIMULATOR_INLINE_BYTES = 1024
# 内联页目标字节数：工具结果的编码上限是 8,000 字符（sico tool_result_chars），
# 超限即截断为预览并迫使智能体转读 artifact；这里留出封装余量。
INLINE_RESULT_BYTES = 7600
# 仅影响 ADE 显示/编辑上下文的字段：内联页只标记存在，完整内容在 artifact。
PARAMETER_CONTEXT_FIELDS = ("display", "editable")


def inline_parameters(rows):
    """参数行内联投影：保留取值相关字段，显示/编辑上下文指向 artifact。"""
    projected = []
    for row in rows:
        item = dict(row)
        for field in PARAMETER_CONTEXT_FIELDS:
            if field in item:
                item[field] = {"status": "artifact_only", "reason": "edit_context"}
        property_row = item.get("instance_property")
        if (isinstance(property_row, dict) and property_row.get("status") == "present"
                and property_row.get("value") == item.get("current")):
            item["instance_property"] = {"status": "equals_current"}
        projected.append(item)
    return projected


def encoded_size(value):
    """与运行期截断口径一致的编码长度（sico tool_message 对 record 的 json 编码）。"""
    return len(json.dumps(value, ensure_ascii=False))


def probe_next_action(result):
    """One corrective action for the two common probe preflight failures."""
    if result.get("ok"):
        return None
    error = str(result.get("error") or "")
    if "effective-cell symbol CDF capture" in error:
        return ("Run inspect_cdf on the master symbol with no instance, then pass "
                "that effective-cell cdf_ref to probe_cdf_parameters.")
    if "does not accept requested" in error:
        return ("Use the live CDF type reported by inspect_cdf for each parameter "
                "instead of inferring it from the requested Python value.")
    return None


def inspection_next_action(result):
    """Correct the frequent target/instance mix-up without another blind read."""
    if result.get("ok"):
        return None
    error = str(result.get("error") or result.get("output") or "")
    if "instance not found" in error:
        return ("Pass the direct instance name in the containing schematic; omit "
                "instance only for the master symbol's cell CDF.")
    return None


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
    if name == "probe_cdf_parameters":
        action = probe_next_action(result)
        if action:
            result["next_action"] = action
    elif name == "inspect_cdf":
        action = inspection_next_action(result)
        if action:
            result["next_action"] = action
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
    if name == "inspect_cdf":
        # 证据文件保留完整归一化捕获；内联结果只放这一页真正要读的内容：
        # 身份、请求的参数与模拟器索引。重复展开 simInfo 的 raw 与解析表会把
        # 每次检查推到 8 KiB 预览上限之外，迫使智能体转去 shell 读 artifact
        # （M4 成本构成：单次 inspect_cdf 59 KB，其中 95% 是同一 plist 的三份拷贝）。
        result["sim_info"] = {"status": "artifact_only", "reason": "capture_is_in_artifact"}
        sims = result.get("simulators") or {}
        items = []
        for item in sims.get("items", []):
            attributes = item.get("attributes")
            if attributes is None:
                # 未解析时 raw 是唯一表示，保留原样（受限时仍走 artifact 截断）。
                items.append({"name": item.get("name"), "raw": item.get("raw")})
            elif len(json.dumps(attributes, ensure_ascii=False)) <= SIMULATOR_INLINE_BYTES:
                items.append({"name": item.get("name"), "attributes": attributes})
            else:
                items.append({
                    "name": item.get("name"),
                    "attributes": "artifact_only",
                    "attribute_count": len(attributes),
                    "attribute_names": sorted(attributes)[:32],
                })
        result["simulators"] = {"status": sims.get("status"), "items": items}
        result["parameters"] = inline_parameters(result.get("parameters") or [])
        if encoded_size(result) > INLINE_RESULT_BYTES:
            # 仍超预算时只留名称与当前值，其余明确指向 artifact。
            result["parameters"] = [
                {"name": row.get("name"), "current": row.get("current")}
                for row in result["parameters"]
            ]
            result["parameters_detail"] = "artifact_only"
        if encoded_size(result) > INLINE_RESULT_BYTES:
            # 参数本身就超预算（大过滤集）：内联只报条目数，完整页在 artifact。
            result["parameters"] = {
                "status": "artifact_only",
                "reason": "inline_response_limit",
                "count": len(result["parameters"]),
            }
    else:
        # Keep the full bounded diagnostic data in evidence, report useful differences inline.
        result.pop("parameters", None)
        result.pop("raw_properties", None)
        result["baseline_parameters"] = "see artifact"
        for step in result.get("trace", []):
            step.pop("values", None)

    if encoded_size(result) > 800000 and name != "inspect_cdf":
        # Complete details remain in the hash-checked artifact; truncation is explicit.
        result["trace"] = [
            dict(label=s["label"], change_count=len(s["changes"])) for s in result.get("trace", [])
        ]
        result["trace_inline_complete"] = False
    if encoded_size(result) > 800000:
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
