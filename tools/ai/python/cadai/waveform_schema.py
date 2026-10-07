"""Bounded waveform capture and deterministic, explicit measurement recipes."""

from .circuit_schema import string_schema, tool
from .circuit_spec_schema import array, enum, obj

WAVE_SCHEMA = "cad.maestro.waveform.v1"
MEASUREMENT_SCHEMA = "cad.waveform.measurements.v1"
MAX_SAMPLES = 65536
REF = string_schema(80, pattern=r"^waveform_[0-9a-f]{64}$")
QUANTITY = obj({"value": {"type": "number"}, "unit": string_schema(32)})
WINDOW = obj({"start": QUANTITY, "stop": QUANTITY})
RECIPE = obj(
    {
        "id": string_schema(96),
        "operation": enum("sample", "minimum", "maximum", "mean", "rms", "crossing"),
        "interpolation": enum("linear"),
        "result_unit": string_schema(32),
        "at": QUANTITY,
        "window": WINDOW,
        "threshold": QUANTITY,
        "direction": enum("rising", "falling", "either"),
        "occurrence": {"type": "integer", "minimum": 1, "maximum": MAX_SAMPLES},
    },
    ("id", "operation", "interpolation", "result_unit"),
)
PAIR_SCHEMA = "cad.waveform.pair-measurements.v1"
EVENT = obj(
    {
        "window": WINDOW,
        "threshold": QUANTITY,
        "direction": enum("rising", "falling", "either"),
        "occurrence": {"type": "integer", "minimum": 1, "maximum": MAX_SAMPLES},
    }
)
PAIR_RECIPE = obj(
    {
        "id": string_schema(96),
        "operation": enum("delay", "gain_sample", "gain_rms"),
        "interpolation": enum("linear"),
        "result_unit": string_schema(32),
        "pairing": enum("explicit_occurrences"),
        "input_event": EVENT,
        "output_event": EVENT,
        "at": QUANTITY,
        "window": WINDOW,
        "denominator_floor": QUANTITY,
    },
    ("id", "operation", "interpolation", "result_unit"),
)

WAVEFORM_TOOLS = [
    tool(
        "read_maestro_waveform",
        "Freeze one saved signal from an exact settled Maestro history/test/corner/point. "
        "Pass the raw simulator analysis result name (e.g. tran), NOT an ADE expression. "
        "RDB supplies the result directory; never changes GUI/OCEAN result selection "
        "or runs simulation. "
        "Real flat waveforms only for measurement, max 65536 samples, no decimation. "
        "Scalar/complex/family/missing/error are explicitly distinguished. "
        "Units come from native vectors.",
        {
            "context_ref": string_schema(128),
            "history": string_schema(),
            "test": string_schema(),
            "corner": string_schema(),
            "point": {"type": "integer", "minimum": 0},
            "analysis": string_schema(96),
            "signal": string_schema(1024),
        },
        ("context_ref", "history", "test", "corner", "point", "analysis", "signal"),
    ),
    tool(
        "measure_waveform",
        "Apply explicit recipes to a frozen waveform_ref offline. sample needs at; "
        "minimum/maximum/mean/rms need window; crossing needs window, threshold, direction, "
        "1-based occurrence. All quantities carry units; interpolation must be linear. "
        "Window mean is time/axis weighted. RMS integrates the square of the linear interpolant. "
        "Crossings use arrival at threshold from a strict side (start-at-threshold not counted). "
        "No extrapolation. Each result has scalar/missing/error status and reason. "
        "No spec qualification.",
        {"waveform_ref": REF, "recipes": array(RECIPE, 32, 1)},
        ("waveform_ref", "recipes"),
    ),
    tool(
        "query_waveform",
        "Page immutable waveform samples with source identity and native axis units, "
        "without Virtuoso.",
        {
            "waveform_ref": REF,
            "offset": {"type": "integer", "minimum": 0, "maximum": MAX_SAMPLES},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        ("waveform_ref",),
    ),
    tool(
        "measure_waveform_pair",
        "Measure two immutable real waveform_refs offline from the SAME exact history/test/corner/"
        "point/analysis and result snapshot. Input/output roles are explicit. "
        "delay requires input_event and output_event "
        "(window, threshold, direction, 1-based occurrence), "
        "pairing=explicit_occurrences "
        "and a time result_unit. Returns SIGNED output crossing time minus input crossing time; "
        "no automatic next-edge pairing. gain_sample requires at; "
        "gain_rms requires one shared window "
        "(includes DC). Gains return output/input, result_unit=1, with compatible voltage/current/"
        "dimensionless units and an explicit nonnegative denominator_floor quantity. "
        "abs(input)<=floor is an error, including equality/zero. "
        "Each waveform uses its own sampling "
        "grid and linear interpolation, no extrapolation. Returns constituent values and sources, "
        "and per-recipe scalar/missing/error status. No complex AC or spec qualification.",
        {"input_ref": REF, "output_ref": REF, "recipes": array(PAIR_RECIPE, 32, 1)},
        ("input_ref", "output_ref", "recipes"),
    ),
]
WAVEFORM_NAMES = frozenset(t["name"] for t in WAVEFORM_TOOLS)
