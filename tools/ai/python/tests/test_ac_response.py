"""Response crossings, complex path branch, gaps and explicit recipe guards."""

import cmath
import math
from copy import deepcopy

import pytest
from ac_fixture import wave
from ac_response_fixture import recipe
from test_circuit_rc_gui import mcp_call
from test_waveform_measure import quantity

from cadai.ac_response import call_response, measure_response
from cadai.ac_response_math import crossings
from cadai.mcp import McpServer
from cadai.result_contract import ResultError
from cadai.result_tools import ResultStore
from cadai.waveform_measure import MeasurementIssue


def measured(samples, specs, first=None):
    second = wave(samples)
    first = first or wave([[100, 1, 0], [1e6, 1, 0]])
    return measure_response(first, second, specs, quantity(0, "V"))


def test_lowpass_exact_complex_crossing_and_units():
    rows, events = measured(
        [[100, 2, 0], [1000, 0, 0]],
        [
            recipe(
                "bandwidth",
                window=dict(start=quantity(0.1, "kHz"), stop=quantity(1, "kHz")),
                reference_gain=quantity(2, "1"),
                drop_db=20 * math.log10(2),
                result_unit="kHz",
            )
        ],
    )
    assert rows[0]["value"] == pytest.approx(0.55)
    assert events[0]["frequency_hz"] == pytest.approx(550)


def test_two_crossings_inside_same_segment_not_lost():
    events = crossings([0, 10], [1 + 0j] * 2, [-2 + 0j, 2 + 0j], 1)
    assert [(e["frequency_hz"], e["direction"]) for e in events] == [
        (2.5, "falling"),
        (7.5, "rising"),
    ]
    # A path tangent to the unit circle inside one segment produces no crossing.
    assert crossings([0, 10], [1 + 0j] * 2, [-1 + 1j, 1 + 1j], 1) == []
    with pytest.raises(MeasurementIssue) as exc:
        crossings([0, 10], [1 + 0j] * 2, [-1 + 1j * (1 - 1e-15), 1 + 1j * (1 - 1e-15)], 1)
    assert exc.value.reason == "crossing_numerically_ambiguous"
    assert (
        len(crossings([0, 10], [1 + 0j] * 2, [-1 + 1j * (1 - 1e-10), 1 + 1j * (1 - 1e-10)], 1)) == 2
    )


def test_knot_crossing_tangent_and_endpoint_rules():
    events = crossings([0, 1, 2, 3, 4], [1 + 0j] * 5, [2 + 0j, 1 + 0j, 0.5 + 0j, 1 + 0j, 2 + 0j], 1)
    assert [e["frequency_hz"] for e in events] == [1, 3]
    assert crossings([0, 1, 2], [1 + 0j] * 3, [2 + 0j, 1 + 0j, 2 + 0j], 1) == []
    assert crossings([0, 1], [1 + 0j] * 2, [1 + 0j, 2 + 0j], 1) == []
    assert crossings([0, 1], [1 + 0j] * 2, [2 + 0j, 1 + 0j], 1) == []


def test_explicit_second_falling_crossing_and_missing_third():
    data = [[100, 2, 0], [1000, 0.5, 0], [2000, 2, 0], [3000, 0.5, 0], [1e6, 0.5, 0]]
    specs = [
        recipe("phase_margin", occurrence=2),
        recipe("phase_margin", id="absent", occurrence=3),
    ]
    rows, events = measured(data, specs)
    assert (
        rows[0]["value"] == 180
        and rows[0]["components"]["selected_crossing"]["frequency_hz"] > 2000
    )
    assert rows[1]["status"] == "missing" and rows[1]["value"] is None
    assert len(events) == 6


def test_phase_continuation_beyond_principal_and_explicit_anchor():
    angles = [0, -80, -160, -240, -280]
    samples = [
        [
            100 + 100 * i,
            2 * cmath.exp(1j * math.radians(a)).real,
            2 * cmath.exp(1j * math.radians(a)).imag,
        ]
        for i, a in enumerate(angles)
    ]
    z = 0.5 * cmath.exp(1j * math.radians(-280))
    samples.append([600, z.real, z.imag])
    win = dict(start=quantity(100, "Hz"), stop=quantity(600, "Hz"))
    rows, _ = measured(
        samples,
        [
            recipe("phase_margin", window=win),
            recipe("phase_margin", id="turn", window=win, phase_anchor_turns=1),
        ],
    )
    assert rows[0]["value"] == pytest.approx(-100)
    assert rows[1]["value"] == pytest.approx(260)
    assert rows[0]["components"]["phase_path"]["crossing_phase_rad"] == pytest.approx(
        math.radians(-280)
    )


def test_loop_sign_applied_before_anchor():
    rows, _ = measured([[100, -2, 0], [1e6, -0.5, 0]], [recipe("phase_margin", loop_sign=-1)])
    assert rows[0]["value"] == 180


def test_mixed_grids_units_and_radian_margin():
    first = wave([[0.1, 1000, 0], [0.9, 1000, 0], [1000, 1000, 0]], x_unit="kHz", y_unit="mV")
    rows, _ = measured(
        [[100, 2, 0], [1000, 1, 0], [1e6, 0.5, 0]],
        [recipe("phase_margin", result_unit="rad")],
        first,
    )
    assert rows[0]["value"] == pytest.approx(math.pi)
    assert rows[0]["components"]["selected_crossing"]["frequency_hz"] == 1000


def test_floor_equality_and_out_of_window_phase_zero():
    # A zero after the selected crossing is irrelevant to its phase path.
    rows, _ = measured([[100, 2, 0], [1000, 0.5, 0], [1e6, -2, 0]], [recipe("phase_margin")])
    assert rows[0]["value"] == 180
    rows, _ = measured(
        [[100, 2, 0], [1000, 0.5, 0], [1e6, 0.5, 0]],
        [recipe("phase_margin", magnitude_floor=quantity(1.1, "1"))],
    )
    assert rows[0]["reason"] == "phase_magnitude_at_or_below_floor_in_path"
    first = wave([[100, 1, 0], [1e6, 1, 0]])
    second = wave([[100, 2, 0], [1e6, 0.5, 0]])
    rows, _ = measure_response(first, second, [recipe("phase_margin")], quantity(1, "V"))
    assert rows[0]["reason"] == "denominator_at_or_below_floor_in_window"


def test_missing_signal_and_bad_units_preserve_null():
    first = wave([[100, 1, 0], [1e6, 1, 0]])
    rows, _ = measure_response(
        first, wave([], status="family"), [recipe("bandwidth")], quantity(0, "V")
    )
    assert rows[0]["reason"] == "output:source_family" and rows[0]["value"] is None
    rows, _ = measured([[100, 2, 0], [1e6, 0.5, 0]], [recipe("bandwidth", result_unit="s")])
    assert rows[0]["reason"] == "unit_incompatible"


def test_bounded_crossing_count():
    with pytest.raises(MeasurementIssue) as exc:
        crossings(
            list(range(300)),
            [1 + 0j] * 300,
            [complex(2 if i % 2 else 0.5, 0) for i in range(300)],
            1,
        )
    assert exc.value.reason == "crossing_count_limit"


def test_phase_ratio_turn_over_pi_uses_two_source_paths():
    # Numerator +170deg and denominator -170deg in a single segment => +340deg,
    # even though ratio endpoints differ by only -20deg.
    z = cmath.exp(1j * math.radians(170))
    first = wave([[100, 1, 0], [500, z.real, -z.imag], [1000, z.real, -z.imag]])
    data = [[100, 2, 0], [500, 2 * z.real, 2 * z.imag], [1000, 0.5 * z.real, 0.5 * z.imag]]
    rows, _ = measured(
        data,
        [recipe("phase_margin", window=dict(start=quantity(100, "Hz"), stop=quantity(1000, "Hz")))],
        first,
    )
    assert rows[0]["value"] == pytest.approx(520)


@pytest.mark.parametrize(
    "case,reason",
    [
        ("denominator", "denominator_at_or_below_floor_in_window"),
        ("phase", "phase_magnitude_at_or_below_floor_in_path"),
        ("plateau", "threshold_plateau"),
        ("lowpass", "lowpass_window_start_not_above_threshold"),
        ("window", "window_out_of_range"),
    ],
)
def test_invalid_paths_not_successful(case, reason):
    first = wave([[100, 1, 0], [1e6, 1, 0]])
    data = [[100, 2, 0], [1e6, 0.5, 0]]
    spec = recipe("phase_margin")
    if case == "denominator":
        first["samples"][-1][1] = -1
    if case == "phase":
        data = [[100, 2, 0], [1000, -2, 0], [1e6, -0.5, 0]]
        spec["occurrence"] = 2
    if case == "plateau":
        data = [[100, 1, 0], [1e6, 1, 0]]
    if case == "lowpass":
        data = [[100, 0.1, 0], [1e6, 0.01, 0]]
        spec = recipe("bandwidth")
    if case == "window":
        spec["window"]["stop"]["value"] = 2e6
    rows, _ = measured(data, [spec], first)
    assert rows[0]["value"] is None and rows[0]["reason"] == reason


def test_resonant_rlc_analytic_bandwidth_and_margin():
    tau = math.sqrt(0.01 * 1e-9)
    damping = 1000 * math.sqrt(1e-9 / 0.01)
    xs = [10 ** (2 + i / 400) for i in range(1601)]
    zs = [
        1 / complex(1 - (2 * math.pi * f * tau) ** 2, 2 * math.pi * f * tau * damping) for f in xs
    ]
    rows, _ = measured(
        [[x, z.real, z.imag] for x, z in zip(xs, zs)], [recipe("bandwidth"), recipe("phase_margin")]
    )
    root = math.sqrt(
        (2 - damping * damping + math.sqrt((damping * damping - 2) ** 2 + 4 * (10**0.3 - 1))) / 2
    )
    assert rows[0]["value"] == pytest.approx(root / (2 * math.pi * tau), rel=5e-5)
    unity = math.sqrt(2 - damping * damping)
    expected = 180 - math.degrees(math.atan2(damping * unity, 1 - unity * unity))
    assert rows[1]["value"] == pytest.approx(expected, abs=0.003)


def test_offline_mcp_report_provenance_and_validation(tmp_path):
    store = ResultStore(tmp_path)
    first = wave([[100, 1, 0], [1e6, 1, 0]])
    second = wave([[100, 2, 0], [1e6, 0.5, 0]])
    a, _ = store.put("ac_waveform", first)
    b, _ = store.put("ac_waveform", second)
    args = dict(
        input_ref=a,
        output_ref=b,
        denominator_floor=quantity(0, "V"),
        recipes=[recipe("phase_margin")],
    )

    class Offline:
        def call(self, *args):
            pytest.fail("response measurement is offline")

    server = McpServer(Offline(), workspace=tmp_path)
    report = mcp_call(server, "measure_ac_response", args)
    assert report["all_measured"] and report["stability_qualified"] is None
    assert mcp_call(McpServer(Offline(), workspace=tmp_path), "measure_ac_response", args) == report
    page = mcp_call(
        server,
        "query_ac_response",
        dict(report_ref=report["report_ref"], entity="crossings", metric="phase_margin", limit=1),
    )
    assert page["total"] == 1
    for change in [
        dict(loop_sign=0),
        dict(phase_mode="principal"),
        dict(characteristic="one_minus_L"),
        dict(phase_anchor_turns=0.5),
        dict(reference_gain=quantity(1, "1")),
    ]:
        bad = deepcopy(args)
        bad["recipes"][0].update(change)
        with pytest.raises(ResultError):
            call_response("measure_ac_response", bad, workspace=tmp_path)
    bad = deepcopy(args)
    bad["recipes"][0].pop("phase_anchor_turns")
    with pytest.raises(ResultError):
        call_response("measure_ac_response", bad, workspace=tmp_path)
