from __future__ import annotations

import json
from pathlib import Path

import pytest

from aivworkbench.ldo_behavior_contract import (
    LDOBehaviorContractError,
    read_behavior_contract,
)


ROOT = Path(__file__).resolve().parents[1] / "recipes" / "ldo"


@pytest.mark.parametrize("name", ("source_behavior_candidate.json", "source_behavior_candidate_aon.json"))
def test_candidate_behavior_contract_is_pending(name: str) -> None:
    contract = read_behavior_contract(ROOT / name)
    assert contract["status"] == "candidate_pending_approval"
    assert contract["verdict_policy"] == {
        "behavior_verdict": "NOT_ESTABLISHED",
        "correlation_status": "BLOCKED_CONTRACT",
        "publication": "human_approval_required",
    }
    assert contract["holdout"]["locked"] is True
    assert contract["holdout"]["public_values_included"] is False
    assert all(item["accepted_absolute_tolerance"] is None for item in contract["metrics"].values())


@pytest.mark.parametrize(
    "mutation",
    (
        lambda value: value.update(status="approved"),
        lambda value: value["behavior"]["enable"].update(threshold_v=1.2),
        lambda value: value["metrics"]["vout"].update(accepted_absolute_tolerance=0.02),
        lambda value: value["verdict_policy"].update(behavior_verdict="PASS"),
    ),
)
def test_candidate_contract_rejects_premature_qualification(tmp_path: Path, mutation) -> None:
    value = json.loads((ROOT / "source_behavior_candidate_aon.json").read_text())
    mutation(value)
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(value))
    with pytest.raises(LDOBehaviorContractError):
        read_behavior_contract(path)
