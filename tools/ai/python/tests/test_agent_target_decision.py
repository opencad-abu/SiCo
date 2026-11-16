"""No-target bridge replies become a bounded target-choice question."""

from __future__ import annotations

from types import SimpleNamespace

from sico.adapters.circuit_decisions import (
    prepare_target_use,
    remember_created_target,
    target_decision,
)
from sico.service.audit import NEW_WINDOW_OPTION


def test_target_decision_lists_open_windows_and_the_new_window_option():
    data = {
        "code": "circuit_requires_target",
        "family": "schematic",
        "candidates": [
            {"library": "AnalogWork", "cell": "amp_tb", "view": "schematic", "editable": True},
            {"library": "smic28hkmg", "cell": "ldo_top", "view": "schematicXL", "editable": False},
        ],
    }
    decision = target_decision(data)
    assert decision["decision_required"] is True
    assert decision["target_ref"].startswith("target:")
    question = decision["selection_question"]
    assert question["header"] == "绑定设计目标"
    assert [option["label"] for option in question["options"]] == [
        "AnalogWork / amp_tb / schematic",
        "smic28hkmg / ldo_top / schematicXL",
        NEW_WINDOW_OPTION,
    ]
    assert question["options"][0]["description"] == "已打开 · 可编辑"
    assert question["options"][1]["description"] == "已打开 · 只读"
    assert question["options"][-1]["description"].startswith("从库中选择")
    assert decision["family"] == "schematic"


def test_target_decision_stays_within_the_audit_option_limit():
    data = {
        "candidates": [
            {"library": f"L{index}", "cell": "c", "view": "schematic", "editable": True}
            for index in range(9)
        ]
        + [{"library": "L0", "cell": "c", "view": "schematic", "editable": True},
           {"library": "", "cell": "", "view": "", "editable": True}],
    }
    options = target_decision(data)["selection_question"]["options"]
    labels = [option["label"] for option in options]
    assert len(options) <= 6
    assert labels.count("L0 / c / schematic") == 1
    assert labels[-1] == NEW_WINDOW_OPTION
    assert all(option["description"] for option in options)


def test_target_decision_tolerates_an_empty_candidate_list():
    options = target_decision({})["selection_question"]["options"]
    assert [option["label"] for option in options] == [NEW_WINDOW_OPTION]
    assert target_decision({})["candidates"] == []


def _arguments():
    return {"spec": {"target": {
        "library": "Work", "cell": "Cell", "view": "schematic",
    }}}


def test_empty_target_is_reused_without_a_review():
    client = SimpleNamespace(
        target_observations={"circuit-target:empty": {
            "target_ref": "circuit-target:empty",
            "target": ["Work", "Cell", "schematic"],
            "content_state": "empty",
        }},
        created_targets={},
    )
    resolved, decision, automatic = prepare_target_use(
        client, _arguments(), current_task="task:1",
        inspect=lambda _target: (_ for _ in ()).throw(AssertionError("inspect")),
    )
    assert decision is None and automatic is True
    assert resolved["target_ref"] == "circuit-target:empty"
    assert resolved["target_action"] == "reuse_empty"


def test_current_task_target_continues_without_a_review():
    target = ["Work", "Cell", "schematic"]
    client = SimpleNamespace(
        target_observations={"circuit-target:owned": {
            "target_ref": "circuit-target:owned",
            "target": target,
            "content_state": "nonempty",
        }},
        created_targets={},
    )
    remember_created_target(client, {
        "task_ref": "task:1", "circuit_ref": "created:partial", "target": target,
    })
    resolved, decision, automatic = prepare_target_use(
        client, _arguments(), current_task="task:1",
        inspect=lambda _target: (_ for _ in ()).throw(AssertionError("inspect")),
    )
    assert decision is None and automatic is True
    assert resolved["target_ref"] == "circuit-target:owned"
    assert resolved["target_action"] == "continue"


def test_external_nonempty_target_asks_once_at_write_preparation():
    target = ["Work", "Cell", "schematic"]
    client = SimpleNamespace(target_observations={}, created_targets={})
    inspected = []

    def inspect(value):
        inspected.append(value)
        return {
            "ok": True,
            "target_ref": "circuit-target:external",
            "target": value,
            "content_state": "nonempty",
            "contents": {"instances": [{"name": "M0"}]},
        }

    resolved, decision, automatic = prepare_target_use(
        client, _arguments(), current_task="task:1", inspect=inspect,
    )
    assert inspected == [target]
    assert automatic is False
    assert resolved == _arguments()
    assert decision["decision_required"] is True
    assert decision["target_ref"] == "circuit-target:external"
    assert client.target_observations["circuit-target:external"]["target"] == target
