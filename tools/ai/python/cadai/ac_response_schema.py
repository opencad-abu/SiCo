"""Explicit low-pass cutoff and loop phase-margin recipes."""

from .ac_schema import AC_REF
from .circuit_schema import string_schema, tool
from .circuit_spec_schema import array, enum, obj
from .waveform_schema import QUANTITY, WINDOW

RESPONSE_SCHEMA = "cad.ac.response-report.v1"
RESPONSE_REF = string_schema(88, pattern=r"^ac_response_[0-9a-f]{64}$")
RESPONSE_RECIPE = obj(
    {
        "id": string_schema(96),
        "operation": enum("bandwidth", "phase_margin"),
        "window": WINDOW,
        "interpolation": enum("cartesian_linear_frequency"),
        "direction": enum("rising", "falling", "either"),
        "occurrence": {"type": "integer", "minimum": 1, "maximum": 256},
        "result_unit": string_schema(32),
        "reference_gain": QUANTITY,
        "drop_db": {"type": "number", "minimum": 0, "maximum": 600},
        "definition": enum("lowpass_cutoff"),
        "characteristic": enum("one_plus_L"),
        "loop_sign": {"type": "integer", "enum": [-1, 1]},
        "phase_anchor_turns": {"type": "integer", "minimum": -16, "maximum": 16},
        "phase_mode": enum("continuous_cartesian_path"),
        "magnitude_floor": QUANTITY,
    },
    ("id", "operation", "window", "interpolation", "direction", "occurrence", "result_unit"),
)
RESPONSE_TOOLS = [
    tool(
        "measure_ac_response",
        "Measure lowpass cutoff or loop phase margin offline from same-source AC input/output "
        "artifacts; denominator_floor must cover the full window. Crossings solve per original "
        "Cartesian-linear source on the union grid with strict side changes; endpoints/tangencies "
        "excluded, plateaus error. bandwidth (definition=lowpass_cutoff) needs positive "
        "reference_gain in unit 1, positive drop_db, falling direction and Hz; returns a cutoff "
        "frequency (window start above threshold), not bandpass width. phase_margin needs "
        "characteristic=one_plus_L, loop_sign +/-1, continuous_cartesian_path, integer "
        "phase_anchor_turns at window start, dimensionless magnitude_floor and deg/rad; returns "
        "pi+continuous phase at the chosen unity crossing, unwrapped.",
        {
            "input_ref": AC_REF,
            "output_ref": AC_REF,
            "denominator_floor": QUANTITY,
            "recipes": array(RESPONSE_RECIPE, 16, 1),
        },
        ("input_ref", "output_ref", "denominator_floor", "recipes"),
        help_text=(
            "No sampled-magnitude or log interpolation is used. Specify crossing direction and "
            "1-based occurrence; there is no implicit first/last/best selection. Use separate "
            "numerator/denominator paths for continuation. Expected coverage and specification "
            "checks happen elsewhere. Results are measurements, not stability certification."),
    ),
    tool(
        "query_ac_response",
        "Page saved response rows or all strict magnitude crossings. Optional metric id filter. "
        "Retain crossing direction/bracket, loop phase branch and failure reason offline.",
        {
            "report_ref": RESPONSE_REF,
            "entity": enum("rows", "crossings"),
            "metric": string_schema(96),
            "offset": {"type": "integer", "minimum": 0, "maximum": 4096},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        ("report_ref",),
    ),
]
RESPONSE_NAMES = frozenset(t["name"] for t in RESPONSE_TOOLS)
