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
        "Measure explicit lowpass cutoff or loop phase margin from same-source complex AC "
        "input/output artifacts offline. Require denominator_floor for the ENTIRE window. "
        "Solve magnitude crossings of each original Cartesian-linear source on their union grid; "
        "no sampled-magnitude or log interpolation. Crossings require strict side change, "
        "exclude window endpoints/tangencies; threshold plateaus are errors. "
        "bandwidth requires definition=lowpass_cutoff, positive reference_gain in unit 1, "
        "positive drop_db, falling direction and Hz result unit. Returns cutoff frequency, "
        "not a bandpass width; window start must be above threshold. "
        "phase_margin requires characteristic=one_plus_L, loop_sign +/-1 applied to output/input, "
        "phase_mode=continuous_cartesian_path, explicit integer phase_anchor_turns "
        "at window start, "
        "dimensionless magnitude_floor and deg/rad. Return pi+continuous phase at selected unity "
        "crossing without wrapping; use separate numerator/denominator paths for continuation. "
        "Specify direction and 1-based occurrence; no implicit first/last/best selection. "
        "Results are measurements, not stability certification or expected coverage/spec checks.",
        {
            "input_ref": AC_REF,
            "output_ref": AC_REF,
            "denominator_floor": QUANTITY,
            "recipes": array(RESPONSE_RECIPE, 16, 1),
        },
        ("input_ref", "output_ref", "denominator_floor", "recipes"),
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
