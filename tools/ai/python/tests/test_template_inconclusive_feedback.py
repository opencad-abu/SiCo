"""Search exhaustion reports known counters and an explicit bounded retry path."""

from template_search_corpus import pool_item, topology

from cadai.template_prepare_feedback import add_next_action
from cadai.template_prepare_schema import serialize_result
from cadai.template_search import search_exact


def test_unknown_evidence_stays_inconclusive_with_actionable_feedback():
    target = topology()
    target["gaps"] = [{"code": "terminal_inventory_unverified"}]
    result = search_exact(target, [pool_item()])
    search = result["search"].copy()
    result = add_next_action(result, {})
    assert result["status"] == "inconclusive" and result["plan"] is None
    assert result["search"] == search
    assert result["next_action"]["input_alternatives"] == [["template_ref"], ["options"]]
    assert "1 unresolved" in result["next_action"]["message"]
    assert "not no_match" in result["next_action"]["message"]
    assert serialize_result(result) == result
