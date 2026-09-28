"""Rule selector model regressions."""

from __future__ import annotations
from drcpy.rule_select_qt import Qt
from drcpy.rule_select_qt import QApplication
from drcpy.rule_groups import RuleGroupInfo
from drcpy.rule_select_view import RuleSelectFilterProxyModel
from drcpy.rule_select_model import RuleSelectModel
from drcpy.rule_selection import RuleSelection


def test_group_cascade_and_child_selection_produce_three_states(
    application: QApplication, groups: RuleGroupInfo
) -> None:
    model = RuleSelectModel(groups)

    model.set_group_selected("METAL", True)
    assert model.group_state("METAL") == Qt.Checked
    assert model.selection() == RuleSelection(("METAL",), ())

    model.set_check_selected("M1.WIDTH", False)
    assert model.group_state("METAL") == Qt.PartiallyChecked
    assert model.selection() == RuleSelection((), ("COMMON.CHECK",))

    model.set_check_selected("COMMON.CHECK", False)
    assert model.group_state("METAL") == Qt.Unchecked
    assert model.selection() == RuleSelection()


def test_item_check_events_cascade_and_shared_checks_stay_in_sync(
    application: QApplication, groups: RuleGroupInfo
) -> None:
    model = RuleSelectModel(groups)
    metal = model.group_item("metal")
    shared_items = model.check_items("common.check")
    assert metal is not None and len(shared_items) == 2

    metal.setCheckState(Qt.Checked)
    assert all(item.checkState() == Qt.Checked for item in shared_items)
    assert model.group_state("SHARED") == Qt.PartiallyChecked

    shared_items[1].setCheckState(Qt.Unchecked)
    assert all(item.checkState() == Qt.Unchecked for item in shared_items)
    assert model.group_state("METAL") == Qt.PartiallyChecked
    assert model.group_state("SHARED") == Qt.Unchecked
    assert model.selection() == RuleSelection((), ("M1.WIDTH",))


def test_overlapping_full_groups_are_emitted_and_cover_shared_check(
    application: QApplication, groups: RuleGroupInfo
) -> None:
    model = RuleSelectModel(groups)
    model.set_group_selected("METAL", True)
    model.set_check_selected("SHARED.ONLY", True)

    assert model.group_state("SHARED") == Qt.Checked
    assert model.selection() == RuleSelection(("METAL", "SHARED"), ())


def test_initial_full_group_and_unknown_names_keep_existing_semantics(
    application: QApplication, groups: RuleGroupInfo
) -> None:
    model = RuleSelectModel(
        groups,
        RuleSelection(("METAL", "OLD.OPAQUE"), ("OLD.CHECK",)),
    )

    assert model.group_state("METAL") == Qt.Checked
    assert model.selection() == RuleSelection(("METAL", "OLD.OPAQUE"), ("OLD.CHECK",))


def test_recursive_filter_does_not_change_selection(
    application: QApplication, groups: RuleGroupInfo
) -> None:
    model = RuleSelectModel(groups)
    proxy = RuleSelectFilterProxyModel()
    proxy.setSourceModel(model)
    model.set_group_selected("METAL", True)
    expected = model.selection()

    proxy.set_query("v2.space")
    assert proxy.rowCount() == 1
    visible_group = proxy.index(0, 0)
    assert visible_group.data() == "VIA"
    assert proxy.rowCount(visible_group) == 1
    assert proxy.index(0, 0, visible_group).data() == "V2.SPACE"
    assert model.selection() == expected

    proxy.set_query("met")
    assert proxy.rowCount() == 1
    assert proxy.rowCount(proxy.index(0, 0)) == 2
    assert model.selection() == expected

    proxy.set_query("[")
    assert proxy.rowCount() == 0
    assert model.selection() == expected
