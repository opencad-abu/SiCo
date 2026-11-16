from __future__ import annotations

from copy import deepcopy
import itertools
from pathlib import Path

import pytest

from aivworkbench.pvt_contract import (
    PVT_BLOCKED_ENVIRONMENT,
    PVT_BLOCKED_INPUT,
    PVT_READY,
    PVT_STALE_ARTIFACT,
    PVTContractError,
    normalize_physical_pvt_matrix,
    preflight_physical_pvt,
)
from aivworkbench.l1 import build_l1_test_plan


def _contract() -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "physical_pvt_exact_cross_product",
        "require_exact_cross_product": True,
        "dimensions": [
            {
                "name": "process",
                "source": "corner",
                "role": "process",
                "values": ["tt", "ss"],
            },
            {
                "name": "temperature_c",
                "source": "corner",
                "role": "temperature",
                "values": [0, 80],
            },
            {
                "name": "vdd",
                "source": "corner",
                "role": "supply",
                "values": [1.1, 1.3],
            },
            {
                "name": "vector",
                "source": "vector_id",
                "role": "vector",
                "values": ["positive", "negative"],
            },
        ],
        "model_binding": {
            "model_file": "models/gpdk045.scs",
            "section_dimension": "process",
            "section_by_value": {"tt": "tt", "ss": "ss"},
        },
        "execution": {
            "simulator": "spectre",
            "launcher": "spectre_pvt_v1",
            "raw_output": "psf",
            "payload_relative_root": "spectre/pvt",
        },
    }


def _cases() -> list[dict[str, object]]:
    result = []
    for index, (process, temperature, vdd, vector) in enumerate(
        itertools.product(("tt", "ss"), (0, 80), (1.1, 1.3), ("positive", "negative"))
    ):
        result.append(
            {
                "id": f"pvt-{index:04d}",
                "corner": {
                    "process": process,
                    "temperature_c": temperature,
                    "vdd": vdd,
                },
                "vector_id": vector,
            }
        )
    return result


def test_normalize_physical_pvt_matrix_binds_all_points_to_explicit_sections() -> None:
    normalized = normalize_physical_pvt_matrix(_contract(), _cases())
    assert normalized["expected_point_count"] == 16
    assert len(normalized["points"]) == 16
    assert normalized["model_binding"]["section_by_value"] == {"tt": "tt", "ss": "ss"}
    assert {point["model_section"] for point in normalized["points"]} == {"tt", "ss"}
    assert normalized["points"][0]["id"] == "pvt-0000"
    assert normalized["points"][-1]["vector_id"] == "negative"


@pytest.mark.parametrize(
    "mutate, message",
    (
        (lambda value: value["dimensions"].pop(0), "requires a process"),
        (lambda value: value["model_binding"]["section_by_value"].pop("ss"), "section_by_value keys"),
        (lambda value: value["model_binding"]["section_by_value"].update({"ss": "tt"}), "sections must be unique"),
        (lambda value: value["execution"].update({"payload_relative_root": "../psf"}), "safe relative path"),
        (lambda value: value["dimensions"][1].update({"values": ["0", "80"]}), "temperature values must be numeric"),
    ),
)
def test_physical_pvt_matrix_rejects_ambiguous_or_unsafe_contracts(mutate, message: str) -> None:
    value = _contract()
    mutate(value)
    with pytest.raises(PVTContractError, match=message):
        normalize_physical_pvt_matrix(value)


def test_physical_pvt_matrix_rejects_case_set_and_metadata_mismatch() -> None:
    cases = _cases()
    cases.pop()
    with pytest.raises(PVTContractError, match="equal the declared exact cross product"):
        normalize_physical_pvt_matrix(_contract(), cases)

    cases = _cases()
    cases[0]["corner"]["extra"] = "bad"
    with pytest.raises(PVTContractError, match="corner fields"):
        normalize_physical_pvt_matrix(_contract(), cases)

    cases = _cases()
    cases[0]["vector_id"] = "unknown"
    with pytest.raises(PVTContractError, match="outside declared values"):
        normalize_physical_pvt_matrix(_contract(), cases)


def test_preflight_returns_blocked_input_without_touching_files(tmp_path: Path) -> None:
    result = preflight_physical_pvt(
        {"kind": "bad"},
        tools={},
        model_root=tmp_path,
        payload_root=tmp_path / "payload",
    )
    assert result["status"] == PVT_BLOCKED_INPUT
    assert result["code"] == "pvt_contract_invalid"
    assert not (tmp_path / "payload").exists()


def test_preflight_requires_spectre_and_model_file(tmp_path: Path) -> None:
    model_root = tmp_path / "models"
    payload_root = tmp_path / "payload"
    model_root.mkdir()
    payload_root.mkdir()
    spectre = tmp_path / "spectre"
    spectre.write_text("#!/bin/sh\n", encoding="utf-8")

    missing_tool = preflight_physical_pvt(
        _contract(),
        tools={},
        model_root=model_root,
        payload_root=payload_root,
    )
    assert missing_tool["status"] == PVT_BLOCKED_ENVIRONMENT
    assert missing_tool["code"] == "pvt_tools_unavailable"

    spectre.chmod(0o755)

    missing_model = preflight_physical_pvt(
        _contract(),
        tools={"spectre": str(spectre)},
        model_root=model_root,
        payload_root=payload_root,
    )
    assert missing_model["status"] == PVT_BLOCKED_ENVIRONMENT
    assert missing_model["code"] == "pvt_model_file_unavailable"


def test_preflight_is_ready_only_when_inputs_are_explicit_and_output_is_fresh(tmp_path: Path) -> None:
    model_root = tmp_path / "models"
    payload_root = tmp_path / "payload"
    model_root.mkdir()
    payload_root.mkdir()
    (model_root / "models").mkdir()
    (model_root / "models" / "gpdk045.scs").write_text(
        "section tt\nsection ss\n", encoding="utf-8"
    )
    spectre = tmp_path / "spectre"
    spectre.write_text("#!/bin/sh\n", encoding="utf-8")
    spectre.chmod(0o755)

    ready = preflight_physical_pvt(
        _contract(),
        tools={"spectre": str(spectre)},
        model_root=model_root,
        payload_root=payload_root,
    )
    assert ready["status"] == PVT_READY
    assert ready["physical_pvt_execution"] == "not_invoked"
    assert ready["design_verdict"] == "NOT_ESTABLISHED"
    assert ready["adapter_registration"] == "pending"
    assert ready["tool_identity_qualification"] == "not_performed_by_preflight"
    assert ready["model_section_verification"] == "pending_adapter"
    assert ready["expected_point_count"] == 16
    assert not (payload_root / "spectre" / "pvt").exists()

    (payload_root / "spectre" / "pvt").mkdir(parents=True)
    stale = preflight_physical_pvt(
        _contract(),
        tools={"spectre": str(spectre)},
        model_root=model_root,
        payload_root=payload_root,
    )
    assert stale["status"] == PVT_STALE_ARTIFACT
    assert stale["code"] == "pvt_output_stale"


def test_preflight_rejects_intermediate_output_symlink(tmp_path: Path) -> None:
    model_root = tmp_path / "models"
    payload_root = tmp_path / "payload"
    model_root.mkdir()
    payload_root.mkdir()
    (model_root / "models").mkdir()
    (model_root / "models" / "gpdk045.scs").write_text("section tt\nsection ss\n", encoding="utf-8")
    spectre = tmp_path / "spectre"
    spectre.write_text("#!/bin/sh\n", encoding="utf-8")
    spectre.chmod(0o755)
    (payload_root / "spectre").symlink_to(tmp_path / "outside", target_is_directory=True)

    result = preflight_physical_pvt(
        _contract(),
        tools={"spectre": str(spectre)},
        model_root=model_root,
        payload_root=payload_root,
    )
    assert result["status"] == PVT_STALE_ARTIFACT
    assert result["code"] == "pvt_output_stale"


def test_preflight_rejects_non_executable_and_symlink_tools(tmp_path: Path) -> None:
    model_root = tmp_path / "models"
    payload_root = tmp_path / "payload"
    model_root.mkdir()
    payload_root.mkdir()
    (model_root / "models").mkdir()
    (model_root / "models" / "gpdk045.scs").write_text(
        "section tt\nsection ss\n", encoding="utf-8"
    )
    tool = tmp_path / "spectre"
    tool.write_text("#!/bin/sh\n", encoding="utf-8")
    result = preflight_physical_pvt(
        _contract(),
        tools={"spectre": str(tool)},
        model_root=model_root,
        payload_root=payload_root,
    )
    assert result["status"] == PVT_BLOCKED_ENVIRONMENT
    assert result["invalid_tools"] == ["spectre"]

    tool.chmod(0o755)
    alias = tmp_path / "spectre-alias"
    alias.symlink_to(tool)
    result = preflight_physical_pvt(
        _contract(),
        tools={"spectre": str(alias)},
        model_root=model_root,
        payload_root=payload_root,
    )
    assert result["status"] == PVT_BLOCKED_ENVIRONMENT
    assert result["invalid_tools"] == ["spectre"]


def test_preflight_rejects_model_file_intermediate_symlink(tmp_path: Path) -> None:
    model_root = tmp_path / "models"
    payload_root = tmp_path / "payload"
    model_root.mkdir()
    payload_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "gpdk045.scs").write_text("section tt\nsection ss\n", encoding="utf-8")
    (model_root / "models").symlink_to(outside, target_is_directory=True)
    tool = tmp_path / "spectre"
    tool.write_text("#!/bin/sh\n", encoding="utf-8")
    tool.chmod(0o755)
    result = preflight_physical_pvt(
        _contract(),
        tools={"spectre": str(tool)},
        model_root=model_root,
        payload_root=payload_root,
    )
    assert result["status"] == PVT_BLOCKED_ENVIRONMENT
    assert result["code"] == "pvt_model_file_unavailable"


def test_ams_execution_requires_runams_and_spectre(tmp_path: Path) -> None:
    value = deepcopy(_contract())
    value["execution"] = {
        "simulator": "ams",
        "launcher": "ams_pvt_v1",
        "raw_output": "psf",
        "payload_relative_root": "ams/pvt",
    }
    model_root = tmp_path / "models"
    payload_root = tmp_path / "payload"
    model_root.mkdir()
    payload_root.mkdir()
    (model_root / "models").mkdir()
    (model_root / "models" / "gpdk045.scs").write_text("section tt\nsection ss\n", encoding="utf-8")
    spectre = tmp_path / "spectre"
    spectre.write_text("#!/bin/sh\n", encoding="utf-8")
    result = preflight_physical_pvt(
        value,
        tools={"spectre": str(spectre)},
        model_root=model_root,
        payload_root=payload_root,
    )
    assert result["status"] == PVT_BLOCKED_ENVIRONMENT
    assert result["required_tools"] == ["runams", "spectre"]


def test_physical_pvt_is_carried_into_canonical_l1_plan() -> None:
    verification = {
        "cases": _cases(),
        "metrics": [{"name": "decision", "kind": "categorical"}],
        "l1": {
            "protocol": "AIVW_L1_EVENT v1 JSONL",
            "require_exact_case_set": True,
            "require_exact_metric_set": True,
            "require_assertion_pass": True,
            "minimum_case_coverage": 1.0,
            "required_metrics": ["decision"],
            "required_checks": ["initial-state"],
            "hold_case": "pvt-0000",
        },
        "physical_pvt": _contract(),
    }
    # The L1 builder intentionally only needs the fields it owns; this fixture
    # proves that physical PVT remains an explicit, separate plan section.
    for case in verification["cases"]:
        case["expected"] = {"decision": "pass"}
        case["assertions"] = ["decision"]
        case["type"] = "pvt_vector"
        case["inputs"] = {"stimulus": case["vector_id"]}
    plan = build_l1_test_plan(verification)
    assert plan["physical_pvt"]["expected_point_count"] == 16
    assert plan["physical_pvt"]["points"][0]["model_section"] == "tt"
