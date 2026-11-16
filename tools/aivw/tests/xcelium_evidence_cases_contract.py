from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest


from aivworkbench.executors.xcelium import (
    _validate_evidence_binding,
)
from aivworkbench.plugins.xcelium_evidence import (
    ADAPTER_NAME,
    get_xcelium_evidence_adapter,
)
from xcelium_evidence_fixtures import _adapter, _plan


def test_adapter_registry_is_exact_and_unknown_names_fail_closed() -> None:
    assert _adapter().name == ADAPTER_NAME
    assert get_xcelium_evidence_adapter("") is None
    assert get_xcelium_evidence_adapter("unknown") is None
    assert get_xcelium_evidence_adapter(None) is None


def test_adapter_binding_is_exact_and_rejects_missing_extra_or_mismatched_fields() -> (
    None
):
    adapter = _adapter()
    plan = _plan()
    binding = adapter.binding_for_plan(plan)
    assert binding == {
        "coverage": {
            "id": "functional",
            "path": "evidence/coverage",
            "producer": "xcelium",
        },
        "waveform": {
            "id": "debug-wave",
            "path": "evidence/waves.shm",
            "producer": "xcelium",
        },
    }
    assert adapter.validate_plan(plan, binding) == []

    broken = deepcopy(binding)
    broken.pop("coverage")
    broken["extra"] = {"id": "extra", "path": "extra", "producer": "xcelium"}
    broken["waveform"] = {
        "id": "wrong",
        "path": "evidence/other.shm",
        "producer": "spectre",
        "arguments": ["-input", "untrusted.tcl"],
    }
    codes = {item["code"] for item in adapter.validate_plan(plan, broken)}
    assert {
        "xcelium_evidence_binding_missing",
        "xcelium_evidence_coverage_binding_missing",
        "xcelium_evidence_binding_extra_type",
        "xcelium_evidence_binding_extra_field",
        "xcelium_evidence_binding_id_mismatch",
        "xcelium_evidence_binding_path_mismatch",
        "xcelium_evidence_binding_producer_invalid",
    } <= codes


@pytest.mark.parametrize("path_value", ("../outside", "/absolute/path"))
def test_generic_binding_rejects_unsafe_paths(tmp_path: Path, path_value: str) -> None:
    plan = _plan()
    binding = _adapter().binding_for_plan(plan)
    binding["coverage"]["path"] = path_value
    _normalized, findings = _validate_evidence_binding(plan, binding, tmp_path)
    assert "evidence_binding_path_invalid" in {item["code"] for item in findings}


def test_generic_binding_rejects_direct_and_intermediate_symlinks(
    tmp_path: Path,
) -> None:
    plan = _plan()
    binding = _adapter().binding_for_plan(plan)
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "evidence").symlink_to(outside, target_is_directory=True)
    _normalized, findings = _validate_evidence_binding(plan, binding, tmp_path)
    symlink_types = {
        item["type"]
        for item in findings
        if item["code"] == "evidence_binding_path_symlink"
    }
    assert symlink_types == {"coverage", "waveform"}


def test_adapter_rejects_unqualified_waveform_format_and_bad_contract_ids() -> None:
    adapter = _adapter()
    plan = _plan(waveform_format="fsdb")
    plan["coverage"]["id"] = "not safe"
    codes = {
        item["code"]
        for item in adapter.validate_plan(plan, adapter.binding_for_plan(plan))
    }
    assert "xcelium_evidence_waveform_format_unqualified" in codes
    assert "xcelium_evidence_coverage_id_invalid" in codes
