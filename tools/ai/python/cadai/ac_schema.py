"""Explicit Cartesian AC capture and point magnitude/phase contracts."""

from .circuit_schema import string_schema, tool
from .circuit_spec_schema import array, enum, obj
from .waveform_schema import MAX_SAMPLES, QUANTITY, WAVEFORM_TOOLS

AC_SCHEMA = "cad.maestro.ac-waveform.v1"
AC_MEASURE_SCHEMA = "cad.ac.measurements.v1"
AC_REF = string_schema(80, pattern=r"^ac_waveform_[0-9a-f]{64}$")
AC_RECIPE = obj(
    {
        "id": string_schema(96),
        "operation": enum("magnitude", "magnitude_db", "phase"),
        "at": QUANTITY,
        "interpolation": enum("cartesian_linear_frequency"),
        "result_unit": string_schema(32),
        "reference": QUANTITY,
        "phase_mode": enum("principal"),
        "magnitude_floor": QUANTITY,
    },
    ("id", "operation", "at", "interpolation", "result_unit"),
)
CAPTURE_FIELDS = dict(WAVEFORM_TOOLS[0]["inputSchema"]["properties"], analysis=enum("ac"))
AC_TOOLS = [
    tool(
        "read_maestro_ac_waveform",
        "Freeze one saved AC signal from exact settled history/test/corner/point. analysis=ac. "
        "Preserve native frequency units and Cartesian [frequency, real, imaginary] samples. "
        "Real AC vectors retain their native type and receive explicit zero imaginary parts. "
        "No simulation or GUI/OCEAN selection changes, no family flattening or decimation. "
        "Limit 65536 samples; nonnegative strictly increasing real frequency axis.",
        CAPTURE_FIELDS,
        tuple(CAPTURE_FIELDS),
    ),
    tool(
        "query_ac_waveform",
        "Page immutable Cartesian AC samples with exact source identity and native units offline.",
        {
            "waveform_ref": AC_REF,
            "offset": {"type": "integer", "minimum": 0, "maximum": MAX_SAMPLES},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        ("waveform_ref",),
    ),
    tool(
        "measure_ac_waveform",
        "Measure one frozen AC phasor at explicit frequencies, offline. Interpolate real/imag "
        "linearly in physical frequency, THEN take magnitude or principal phase; no log-axis "
        "or polar interpolation/extrapolation. magnitude uses compatible native units. "
        "magnitude_db requires an explicit positive reference quantity and dB result, "
        "20*log10(abs(z)/reference). phase needs phase_mode=principal, deg/rad and explicit "
        "nonnegative magnitude_floor; abs(z)<=floor is an error. Principal interval (-180,180] "
        "degrees or (-pi,pi] radians; no unwrap. This is absolute signal phase, not transfer gain.",
        {"waveform_ref": AC_REF, "recipes": array(AC_RECIPE, 32, 1)},
        ("waveform_ref", "recipes"),
    ),
    tool(
        "measure_ac_transfer",
        "Measure complex output/input on matching exact result/test/corner/point/analysis "
        "AC artifacts. Each source is interpolated on its own original grid at the same "
        "physical frequency before division. Compatible V/V or A/A or dimensionless only. "
        "Require explicit nonnegative denominator_floor in input-compatible units. "
        "Magnitude uses result_unit=1; dB uses explicit dimensionless positive reference "
        "with 20*log10 amplitude convention. Phase uses principal deg/rad and dimensionless "
        "magnitude_floor. No bandwidth, phase margin, unwrap or spec qualification.",
        {
            "input_ref": AC_REF,
            "output_ref": AC_REF,
            "denominator_floor": QUANTITY,
            "recipes": array(AC_RECIPE, 32, 1),
        },
        ("input_ref", "output_ref", "denominator_floor", "recipes"),
    ),
]
AC_NAMES = frozenset(t["name"] for t in AC_TOOLS)
