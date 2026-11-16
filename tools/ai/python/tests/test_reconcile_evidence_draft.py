"""Reconcile decisions offer a draft built from the recorded call, not from guesses."""

from sico_ui.reconcile_actions import default_evidence


def test_completed_draft_uses_the_recorded_result():
    operation = {"id": "call-1", "name": "preview_simulation_recipe",
                 "request_id": "prep-1", "target": ["SicoTest", "tb", "schematic"],
                 "result": {"status": "needs_reconcile",
                            "summary": "工具异常（CircuitSpecError）"}}
    text = default_evidence("completed", operation)
    assert "preview_simulation_recipe（call-1）已执行" in text
    assert "needs_reconcile" in text and "CircuitSpecError" in text
    assert "请求号：prep-1" in text
    assert "目标：SicoTest/tb/schematic" in text
    assert len(text) <= 3980


def test_not_executed_draft_states_no_replay():
    text = default_evidence("not_executed", {"id": "call-1", "name": "x"})
    assert "未执行或被放弃；不重放该请求" in text
    assert "结果见会话记录" in text


def test_draft_stays_bounded_without_an_operation():
    text = default_evidence("completed")
    assert text.startswith("按调用记录核对：工具已执行")
    assert len(text) <= 3980
