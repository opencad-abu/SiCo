import hashlib
from pathlib import Path

import pytest
from sico.adapters.destructive_reviews import skill_gate

from cadai.destructive_skill import (
    IMPORT_APPROVAL,
    ReviewPreparationError,
)
from cadai.destructive_skill import (
    prepare as prepare_skill_review,
)
from cadai.layout.import_plan import build_import_plan


def make_plan(tmp_path):
    workspace = tmp_path / "job"
    output = workspace / "output"
    output.mkdir(parents=True)
    content = b"GDS\n"
    (output / "layout.gds").write_bytes(content)
    mapping = tmp_path / "layer.map"
    mapping.write_text("M1 61 0\n", encoding="utf-8")
    value = {
        "state": "succeeded",
        "job_id": "layout-" + "a" * 32,
        "artifacts": [{
            "role": "layout",
            "path": "output/layout.gds",
            "format": "gds",
            "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content),
        }],
    }
    plan = build_import_plan(
        value,
        workspace=workspace,
        target_library="SicoLayout_test",
        target_cell="TOP",
        target_view="layout",
        layer_map=str(mapping),
        expected_job_id=value["job_id"],
    )
    return workspace, plan


class Registry:
    def __init__(self, status):
        self.destructive_review_validator = lambda kind, digest, context: status


@pytest.mark.parametrize("source", [
    "(xstInDoTranslate)",
    "(xoasInDoTranslate)",
    '(strmin "layout.gds" "WORK" "TOP" "layout")',
])
def test_stream_import_calls_require_review(tmp_path, source):
    review = prepare_skill_review("eval_skill", {"code": source}, tmp_path)
    assert review["review_kind"] == "skill_deletion"
    assert review["review"]["calls"]


def test_generated_layout_import_binds_plan_digest(tmp_path):
    workspace, plan = make_plan(tmp_path)
    review = prepare_skill_review(
        "load_skill_file", {"path": plan["skill_file"]}, workspace
    )
    assert review["review_kind"] == "skill_deletion"
    assert review["review_scope"] == "layout_import"
    assert review["review_digest"] == plan["approval_plan_digest"]
    assert review["approval_label"] == IMPORT_APPROVAL
    assert review["selection_question"]["header"] == "版图导入审阅"
    assert review["review"]["layout_import"]["plan_id"] == plan["plan_id"]
    assert "xstInDoTranslate" in review["review"]["calls"]
    assert review["review"]["target"] == plan["skill_file"]


def test_generated_layout_import_reverifies_before_review(tmp_path):
    workspace, plan = make_plan(tmp_path)
    Path(plan["source"]["path"]).write_bytes(b"CHANGED\n")
    with pytest.raises(ReviewPreparationError) as caught:
        prepare_skill_review(
            "load_skill_file", {"path": plan["skill_file"]}, workspace
        )
    assert caught.value.code == "layout_import_plan_invalid"

    workspace, plan = make_plan(tmp_path / "marker")
    Path(plan["completion"]["marker_file"]).write_text("completed\n", encoding="utf-8")
    with pytest.raises(ReviewPreparationError) as caught:
        prepare_skill_review(
            "load_skill_file", {"path": plan["skill_file"]}, workspace
        )
    assert caught.value.code == "layout_import_plan_invalid"


def test_skill_gate_uses_plan_digest_and_fails_closed(tmp_path):
    workspace, plan = make_plan(tmp_path)
    arguments = {"path": plan["skill_file"]}
    blocked, _ = skill_gate(
        Registry("missing"), "load_skill_file", arguments, None, workspace
    )
    assert blocked.status == "waiting_user"
    assert blocked.data["review_digest"] == plan["approval_plan_digest"]

    approved = {"path": plan["skill_file"],
                "approval_digest": plan["approval_plan_digest"]}
    blocked, dispatched = skill_gate(
        Registry("approved"), "load_skill_file", approved, None, workspace
    )
    assert blocked is None
    assert dispatched == {"path": plan["skill_file"]}

    blocked, _ = skill_gate(
        Registry("approved"), "load_skill_file",
        {"path": plan["skill_file"], "approval_digest": "0" * 64}, None, workspace
    )
    assert blocked.status == "tool_error"
    assert blocked.data["code"] == "delete_review_mismatch"

    Path(plan["source"]["path"]).write_bytes(b"CHANGED\n")
    blocked, _ = skill_gate(
        Registry("approved"), "load_skill_file", approved, None, workspace
    )
    assert blocked.status == "tool_error"
    assert blocked.data["code"] == "layout_import_plan_invalid"
    assert blocked.data["operation_dispatched"] is False
