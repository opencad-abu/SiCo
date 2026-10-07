"""Explicit waveform specification contracts; observed coverage is never the expectation."""

from .circuit_schema import string_schema, tool
from .circuit_spec_schema import array, enum, obj
from .waveform_schema import QUANTITY, RECIPE
from .waveform_spec_kinds import RECIPE_SCHEMAS

SPEC_REPORT_SCHEMA = "cad.waveform.spec-report.v1"
RESULT_REF = string_schema(80, pattern=r"^results_[0-9a-f]{64}$")
MEASUREMENT_REF = string_schema(
    96, pattern=r"^((wave|pair|ac)_measurements|ac_response)_[0-9a-f]{64}$"
)
REPORT_REF = string_schema(96, pattern=r"^wave_specs_[0-9a-f]{64}$")
COORD = obj(
    {"test": string_schema(), "corner": string_schema(), "point": {"type": "integer", "minimum": 0}}
)


# Expose the bounded union to MCP; validate against the concrete kind before use.
def _recipe_properties():
    props = {}
    for schema in RECIPE_SCHEMAS.values():
        for key, rule in schema["properties"].items():
            if key not in props:
                props[key] = rule
            elif "enum" in rule:
                props[key] = enum(*dict.fromkeys(props[key]["enum"] + rule["enum"]))
    return props


RECIPE_UNION = obj(_recipe_properties(), RECIPE["required"])
SPEC = obj(
    {
        "test": string_schema(),
        "analysis": string_schema(96),
        "kind": enum(*RECIPE_SCHEMAS),
        "signals": obj({k: string_schema(1024) for k in ("signal", "input", "output")}, ()),
        "recipe": RECIPE_UNION,
        "denominator_floor": QUANTITY,
        "unit": string_schema(32),
        "lower": {"type": "number"},
        "upper": {"type": "number"},
        "lower_inclusive": {"type": "boolean"},
        "upper_inclusive": {"type": "boolean"},
    },
    ("test", "analysis", "kind", "signals", "recipe", "unit"),
)
CONTRACT = obj(
    {
        "source": string_schema(1024),
        "expected_test_points": array(COORD, 2048, 1),
        "specifications": array(SPEC, 64, 1),
    }
)
SPEC_TOOLS = [
    tool(
        "evaluate_waveform_specs",
        "Qualify immutable real/AC measurement reports against an EXPLICIT contract. "
        "Anchor all reports to one exact results_ reference. Contract supplies expected "
        "test/corner/"
        "point coverage, test/analysis/kind/signals, full recipe including id, and unit/bounds. "
        "Kinds: single, pair, ac_signal, ac_transfer, ac_response. Paired AC specifications "
        "must also supply denominator_floor matching the report exactly. Full recipe identity "
        "includes dB reference, phase mode/branch, loop sign, crossing selection and definition. "
        "Recipe and signal identity must match; never select by metric id alone. "
        "Convert compatible SI prefixes and AC deg/rad without phase wrapping; "
        "no dB/linear conversion. "
        "Return pass/fail/incomplete, separate missing/error counts and worst margin per metric. "
        "No coverage/spec inference or simulation. qualification_scope=explicit_contract_only. "
        "A passing AC contract is not stability certification.",
        {
            "result_ref": RESULT_REF,
            "measurement_refs": array(MEASUREMENT_REF, 128),
            "contract": CONTRACT,
        },
        ("result_ref", "measurement_refs", "contract"),
    ),
    tool(
        "query_waveform_spec_report",
        "Page saved waveform specification rows/summaries/coordinates offline. Filter rows by "
        "test, metric id, corner, point or pass/fail/missing/error status; provenance and "
        "raw units "
        "remain available. A missing/error row never contains a fabricated zero value.",
        {
            "report_ref": REPORT_REF,
            "entity": enum("rows", "summaries", "coordinates"),
            "test": string_schema(),
            "metric": string_schema(96),
            "corner": string_schema(),
            "point": {"type": "integer", "minimum": 0},
            "status": enum("pass", "fail", "missing", "error"),
            "offset": {"type": "integer", "minimum": 0, "maximum": 4096},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        ("report_ref",),
    ),
]
SPEC_NAMES = frozenset(t["name"] for t in SPEC_TOOLS)
