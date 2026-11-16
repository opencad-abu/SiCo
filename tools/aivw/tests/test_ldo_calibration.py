from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from aivworkbench.ldo_calibration import (
    LDOCalibrationError,
    read_calibration_handoff,
    validate_calibration_handoff,
)


ROOT = Path(__file__).resolve().parents[1]
HANDOFF = ROOT / "recipes" / "ldo" / "source_behavior_calibration_handoff.json"


def test_source_behavior_calibration_handoff_is_observation_only() -> None:
    value = read_calibration_handoff(HANDOFF)

    assert value["status"] == "observed_pending_contract"
    assert value["verdict_policy"] == {
        "behavior_verdict": "NOT_ESTABLISHED",
        "correlation_status": "BLOCKED_CONTRACT",
        "tolerance_evaluation": "not_performed",
        "publication": "human_approval_required",
    }
    assert {run["topology"] for run in value["runs"]} == {"LDO_MASTER", "LDO_AON"}
    assert [run["case_count"] for run in value["runs"]] == [18, 10]
    assert [run["psf_artifact_count"] for run in value["runs"]] == [36, 20]
    assert all(run["source_generation"] == run["source_generation_before"] == run["source_generation_after"] for run in value["runs"])
    assert "aon_en_threshold" in value["contract_items_pending"]
    assert all(run["measurement_contract"]["tolerance_status"] == "BLOCKED_CONTRACT" for run in value["runs"])


def _redigest(value: dict) -> dict:
    body = {key: item for key, item in value.items() if key != "handoff_digest"}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    value["handoff_digest"] = "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return value


@pytest.mark.parametrize(
    "mutation",
    (
        lambda value: value["verdict_policy"].update(behavior_verdict="PASS"),
        lambda value: value["runs"][0].update(psf_artifact_count=0),
        lambda value: value["runs"][0]["observations"].update(sha256="0" * 64),
        lambda value: value["runs"].__setitem__(1, copy.deepcopy(value["runs"][0])),
        lambda value: value["runs"][0].update(source_generation_after="0" * 64),
    ),
)
def test_calibration_handoff_rejects_tampering(mutation) -> None:
    value = json.loads(HANDOFF.read_text(encoding="utf-8"))
    mutation(value)
    _redigest(value)
    with pytest.raises(LDOCalibrationError):
        validate_calibration_handoff(value)


def test_calibration_handoff_rejects_missing_case(tmp_path: Path) -> None:
    value = json.loads(HANDOFF.read_text(encoding="utf-8"))
    value["runs"][0]["case_ids"].pop()
    _redigest(value)
    with pytest.raises(LDOCalibrationError):
        validate_calibration_handoff(value)
