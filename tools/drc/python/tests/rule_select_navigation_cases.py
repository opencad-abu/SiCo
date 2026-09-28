"""Rule selector navigation regressions."""

from __future__ import annotations
from pathlib import Path
import pytest
from PyQt5.QtCore import QPoint, QPointF
from drcpy.rule_select_qt import Qt
from drcpy.rule_select_qt import QWheelEvent
from drcpy.rule_select_qt import QApplication
from drcpy.rule_groups import RuleGroupInfo
from drcpy.rule_select_dialog import RuleSelectDialog


def test_dialog_installs_wheel_filter_only_while_visible(
    application: QApplication, groups: RuleGroupInfo, tmp_path: Path
) -> None:
    dialog = RuleSelectDialog(groups, output_path=tmp_path / "unused.tsv")
    assert dialog._wheel_filter is None

    dialog.show()
    application.processEvents()
    event_filter = dialog._wheel_filter
    assert event_filter is not None
    assert event_filter._application is application

    dialog.hide()
    application.processEvents()
    assert event_filter._application is None

    dialog.show()
    application.processEvents()
    assert dialog._wheel_filter is event_filter
    assert event_filter._application is application
    dialog.close()
    application.processEvents()
    assert event_filter._application is None


@pytest.mark.parametrize(
    ("event_target", "pixel_delta", "angle_delta"),
    (
        ("viewport", QPoint(0, -40), QPoint()),
        ("viewport", QPoint(), QPoint(0, -15)),
        ("window", QPoint(), QPoint(0, -120)),
    ),
)
def test_rule_tree_mouse_wheel_scrolls_expanded_checks(
    application: QApplication,
    tmp_path: Path,
    event_target: str,
    pixel_delta: QPoint,
    angle_delta: QPoint,
) -> None:
    group_names = tuple(f"GROUP_{number:02d}" for number in range(8))
    many_groups = RuleGroupInfo(
        groups=group_names,
        counts={name: 40 for name in group_names},
        members={
            name: tuple(f"{name}.CHECK_{number:03d}" for number in range(40))
            for name in group_names
        },
        source="wheel-test",
    )
    dialog = RuleSelectDialog(many_groups, output_path=tmp_path / "unused.tsv")
    dialog.show()
    dialog.tree.expandAll()
    application.processEvents()
    scroll_bar = dialog.tree.verticalScrollBar()
    position = QPointF(dialog.tree.viewport().rect().center())
    event = QWheelEvent(
        position,
        QPointF(dialog.tree.viewport().mapToGlobal(position.toPoint())),
        pixel_delta,
        angle_delta,
        Qt.NoButton,
        Qt.NoModifier,
        Qt.ScrollUpdate,
        False,
    )

    assert scroll_bar.maximum() > 0
    assert scroll_bar.value() == 0
    target = (
        dialog.tree.viewport() if event_target == "viewport" else dialog.windowHandle()
    )
    assert target is not None
    QApplication.sendEvent(target, event)
    application.processEvents()

    row_height = dialog.tree.sizeHintForRow(0)
    if pixel_delta.y():
        expected = -pixel_delta.y()
    else:
        expected = round(
            -angle_delta.y()
            * max(1, QApplication.wheelScrollLines())
            * row_height
            / 120
        )
        expected = max(1, expected)
    assert event.isAccepted()
    assert scroll_bar.value() == expected
    dialog.close()
    application.processEvents()
