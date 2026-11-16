"""Floor-window reference-spread accounting."""

from __future__ import annotations

import pytest

from aivworkbench.floor_sensitivity import case_spread
from aivworkbench.waveform_metrics import Waveform


def _policy(voltage_absolute: float, current_absolute: float):
    return {"correlation": {
        "voltage": {"unit": "V", "relative": 0.05, "absolute": voltage_absolute},
        "current": {"unit": "A", "relative": 0.05, "absolute": current_absolute},
        "time": {"unit": "s", "relative": 0.05, "absolute": 1e-7},
    }}


def test_spread_splits_binding_and_non_binding_windows():
    reference = Waveform((0.0, 1.0, 2.0), {"VOUT": "V", "VPROBE:p": "A"},
                         {"VOUT": (0.005, 0.5, 0.005), "VPROBE:p": (1e-6, 100e-6, 1e-6)})
    crosscheck = Waveform((0.0, 1.0, 2.0), {"VOUT": "V", "VPROBE:p": "A"},
                          {"VOUT": (0.015, 0.56, 0.001), "VPROBE:p": (3e-6, 100e-6, 0.5e-6)})
    result = case_spread(reference, crosscheck, _policy(0.02, 1e-6), output="VOUT", current="VPROBE:p")
    assert result["voltage"]["floor_binding_samples"] == 2
    assert result["voltage"]["binding_window_spread"] == pytest.approx(0.01)
    assert result["voltage"]["max_reference_spread"] == pytest.approx(0.06)
    assert result["current"]["floor_binding_samples"] == 2
    assert result["current"]["binding_window_spread"] == pytest.approx(2e-6)
    assert result["current"]["max_reference_spread"] == pytest.approx(2e-6)


def test_equal_references_have_zero_spread():
    wave = Waveform((0.0, 1.0), {"VOUT": "V", "VPROBE:p": "A"},
                    {"VOUT": (0.0, 1.0), "VPROBE:p": (0.0, 1e-3)})
    result = case_spread(wave, wave, _policy(0.02, 1e-6), output="VOUT", current="VPROBE:p")
    assert result["voltage"]["max_reference_spread"] == 0.0
    assert result["voltage"]["binding_window_spread"] == 0.0
    assert result["current"]["binding_window_spread"] == 0.0
