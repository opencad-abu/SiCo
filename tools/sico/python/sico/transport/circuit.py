"""Finite native calls with literal data; the wire never carries executable SKILL."""

import json
import math
import re

from cadai.circuit_spec_schema import CircuitSpecError

METHOD = "circuit_call"
# Only these shared workflow procedures are callable. Nested calls are forbidden.
ARITIES = {
    "aiCopilotResolveResources": (3, 3),
    "aiCdfUpdatePrepare": (4, 4),
    "aiCdfUpdateApply": (2, 2),
    "aiLibraryPrepare": (5, 5),
    "aiLibraryCreate": (2, 2),
    "aiCircuitBeginTask": (3, 3),
    "aiCreatePrepare": (4, 6),
    "aiCrInspectTarget": (3, 3),
    "aiCreateExecute": (3, 4),
    "aiCreateInspect": (1, 1),
    "aiCrExportSvg": (2, 2),
    "aiTemplateSymbolPreview": (11, 12),
    "aiTemplateSymbolCreate": (3, 3),
    "aiDrawSymbol": (3, 3),
    "aiTemplateSymbolInspect": (1, 1),
    "aiTemplateSymbolBinding": (1, 1),
    "aiConfigCreate": (7, 9),
    "aiConfigInspect": (1, 1),
    "aiSimCreate": (5, 5),
    "aiSimModels": (1, 1),
    "aiSimInspect": (2, 2),
    "aiSimRun": (3, 3),
    "aiSimStatus": (1, 1),
    "aiSimStop": (1, 1),
    "aiPdkCdfInspect": (4, 4),
    "aiPdkCdfCase": (7, 7),
    "aiCdfRead": (2, 2),
    "aiCdfDrop": (1, 1),
    "aiPdkCapture": (6, 8),
    "aiPdkRead": (2, 2),
    "aiPdkDrop": (1, 1),
    "aiCrBindingGeometry": (4, 4),
    "aiCrListExtensions": (4, 4),
    "aiCopilotDescribe": (4, 4),
    "aiCopilotListTargets": (1, 1),
    "aiCopilotListLibraries": (1, 1),
    "aiCopilotListCells": (1, 1),
    "aiCopilotListViews": (2, 3),
    "aiCopilotOpenTarget": (4, 5),
    "aiProjectClaim": (3, 3),
    "aiCopilotBindResults": (3, 3),
    "aiResultsCapture": (3, 3),
    "aiResultsPage": (2, 2),
    "aiResultsRelease": (1, 1),
    "aiWaveCapture": (8, 8),
    "aiAcCapture": (8, 8),
    "aiWavePage": (2, 2),
    "aiWaveRelease": (1, 1),
}
WRITES = frozenset(
    {
        "aiCdfUpdateApply",
        "aiLibraryCreate",
        "aiCreateExecute",
        "aiTemplateSymbolCreate",
        "aiDrawSymbol",
        "aiConfigCreate",
        "aiSimCreate",
        "aiSimRun",
    }
)
MAX_WIRE = 180000


def literal_call(code):
    """Parse only the common compilers' root-call/list/scalar grammar, never eval it."""
    if not isinstance(code, str) or len(code.encode()) > 65536:
        raise CircuitSpecError("native call exceeds 64 KiB")
    tokens = []
    pos = 0
    decoder = json.JSONDecoder()
    while pos < len(code):
        if code[pos].isspace():
            pos += 1
            continue
        if code[pos] == '"':
            value, end = decoder.raw_decode(code[pos:])
            tokens.append(("value", value))
            pos += end
            continue
        match = re.match(
            r"[A-Za-z_][A-Za-z0-9_]*|[()]|-?(?:[0-9]+)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", code[pos:]
        )
        if not match:
            raise CircuitSpecError("native compiler emitted unsupported syntax")
        word = match[0]
        tokens.append(("token", word))
        pos += len(word)
    index = 0

    def value(depth=0):
        nonlocal index
        if depth > 24 or index >= len(tokens):
            raise CircuitSpecError("invalid native literal tree")
        kind, item = tokens[index]
        index += 1
        if kind == "value":
            return item
        if item == "list":
            if index >= len(tokens) or tokens[index] != ("token", "("):
                raise CircuitSpecError("invalid literal list")
            index += 1
            result = []
            while index < len(tokens) and tokens[index] != ("token", ")"):
                result.append(value(depth + 1))
            if index >= len(tokens):
                raise CircuitSpecError("unterminated literal list")
            index += 1
            return result
        if item in {"nil", "t"}:
            return None if item == "nil" else True
        try:
            return float(item) if any(c in item for c in ".eE") else int(item)
        except ValueError as exc:
            raise CircuitSpecError("nested native procedure or symbol is forbidden") from exc

    if len(tokens) < 3 or tokens[0][1] not in ARITIES or tokens[1] != ("token", "("):
        raise CircuitSpecError("native procedure is not registered")
    function = tokens[0][1]
    index = 2
    values = []
    while index < len(tokens) and tokens[index] != ("token", ")"):
        values.append(value())
    if index >= len(tokens) or index != len(tokens) - 1 or tokens[index] != ("token", ")"):
        raise CircuitSpecError("trailing or incomplete native expression")
    return function, values


def encode_value(value, depth=0):
    if depth > 24:
        raise ValueError("native literal nesting limit")
    if value is None or value is False:
        return "n"
    if value is True:
        return "t"
    if isinstance(value, str):
        if "\0" in value or len(value.encode()) > 65536:
            raise ValueError("invalid native string")
        return "s" + value.encode("utf-8").hex()
    if isinstance(value, list):
        if len(value) > 4096:
            raise ValueError("native list limit")
        return ":".join(["l" + str(len(value)), *(encode_value(v, depth + 1) for v in value)])
    if type(value) is int and abs(value) <= 2147483647:
        return "i" + str(value)
    if type(value) is float and math.isfinite(value):
        return "f" + repr(value)
    raise ValueError("unsupported native literal type")


def wire_arguments(arguments):
    if not isinstance(arguments, dict) or set(arguments) != {
        "function",
        "values",
        "claim",
        "expected",
    }:
        raise ValueError("invalid native call envelope")
    function, values = arguments["function"], arguments["values"]
    if not isinstance(function, str) or function not in ARITIES or not isinstance(values, list):
        raise ValueError("unregistered native procedure")
    low, high = ARITIES[function]
    if not low <= len(values) <= high:
        raise ValueError("invalid native procedure arity")
    claim, expected = arguments["claim"], arguments["expected"]
    if claim is not None and (
        not isinstance(claim, list)
        or len(claim) != 3
        or not all(isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", v) for v in claim)
    ):
        raise ValueError("invalid native claim")
    if expected is not None and (
        not isinstance(expected, list)
        or len(expected) != 7
        or not isinstance(expected[0], str)
        or not expected[0].startswith("task:")
        or expected[1] not in {"foreground", "background"}
        or not isinstance(expected[2], list)
        or len(expected[2]) != 3
        or not all(
            isinstance(v, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.@-]{0,95}", v)
            for v in expected[2]
        )
        or expected[3] not in {"schematic", "schematicSymbol", "config", "maestro", "library"}
        or not isinstance(expected[4], str)
        or not isinstance(expected[5], str)
        or not expected[5].startswith("/")
        or not (expected[6] is None or isinstance(expected[6], str) and expected[6].startswith("/"))
    ):
        raise ValueError("invalid expected task/target binding")
    if function in WRITES and (claim is None or expected is None):
        raise ValueError("write requires claim and expected target")
    payload = encode_value([values, claim, expected])
    if len(payload) > MAX_WIRE:
        raise ValueError("native wire payload limit")
    return [function, payload]
