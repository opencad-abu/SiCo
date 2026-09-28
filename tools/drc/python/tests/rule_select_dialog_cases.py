"""Rule selector dialog regressions."""

from __future__ import annotations
from pathlib import Path
import pytest
from drcpy.rule_select_qt import Qt
from drcpy.rule_select_qt import QApplication
from drcpy.rule_groups import RuleGroupInfo
from drcpy.rule_select_dialog import RuleSelectDialog
from drcpy.rule_selection import RuleSelection


def test_select_and_clear_visible_touch_only_filtered_checks(
    application: QApplication, groups: RuleGroupInfo, tmp_path: Path
) -> None:
    dialog = RuleSelectDialog(groups, output_path=tmp_path / "unused.tsv")
    dialog.set_filter("space")

    dialog.set_visible_selected(True)
    assert dialog.model.selection() == RuleSelection(("VIA",), ())

    dialog.set_filter("v1")
    dialog.set_visible_selected(False)
    assert dialog.model.group_state("VIA") == Qt.PartiallyChecked
    assert dialog.model.selection() == RuleSelection((), ("V2.SPACE",))
    dialog.close()
    application.processEvents()


def test_dialog_uses_configured_logo_in_title(
    application: QApplication,
    groups: RuleGroupInfo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOGO", raising=False)
    monkeypatch.delenv("COMPANY", raising=False)
    dialog = RuleSelectDialog(groups, output_path=tmp_path / "unused.tsv")

    assert dialog.windowTitle() == "SiCo::DRC Rule Select"

    dialog.close()
    application.processEvents()

    monkeypatch.setenv("LOGO", "ACME")
    dialog = RuleSelectDialog(groups, output_path=tmp_path / "unused.tsv")

    assert dialog.windowTitle() == "ACME::DRC Rule Select"

    dialog.close()
    application.processEvents()


def test_opaque_group_can_be_selected_and_cleared_when_visible(
    application: QApplication, groups: RuleGroupInfo, tmp_path: Path
) -> None:
    dialog = RuleSelectDialog(groups, output_path=tmp_path / "unused.tsv")
    dialog.set_filter("opaque")
    dialog.set_visible_selected(True)
    assert dialog.model.selection() == RuleSelection(("OPAQUE",), ())
    assert dialog.model.group_state("OPAQUE") == Qt.Checked

    dialog.set_visible_selected(False)
    assert dialog.model.selection() == RuleSelection()
    dialog.close()
    application.processEvents()


def test_apply_writes_result_and_accepts_dialog(
    application: QApplication, groups: RuleGroupInfo, tmp_path: Path
) -> None:
    output = tmp_path / "applied.tsv"
    dialog = RuleSelectDialog(groups, output_path=output)
    dialog.model.set_group_selected("METAL", True)

    assert dialog.apply_selection()
    assert dialog.applied is True
    assert dialog.result() == dialog.Accepted
    assert output.read_text(encoding="ascii") == ("#status\tapplied\nGROUP\tMETAL\n")
    application.processEvents()


def test_empty_apply_keeps_dialog_open_and_writes_nothing(
    application: QApplication,
    groups: RuleGroupInfo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "empty.tsv"
    dialog = RuleSelectDialog(groups, output_path=output)
    warnings: list[tuple] = []
    monkeypatch.setattr(
        "drcpy.rule_select_dialog.notice",
        lambda *args, **kwargs: warnings.append(args),
    )

    assert dialog.apply_button.isEnabled()
    assert dialog.apply_selection() is False
    assert warnings
    assert not output.exists()
    assert dialog.applied is False
    dialog.close()
    application.processEvents()


@pytest.mark.parametrize("action", ["cancel", "close"])
def test_cancel_and_window_close_do_not_write_result(
    application: QApplication,
    groups: RuleGroupInfo,
    tmp_path: Path,
    action: str,
) -> None:
    output = tmp_path / f"{action}.tsv"
    dialog = RuleSelectDialog(groups, output_path=output)
    dialog.model.set_group_selected("METAL", True)
    dialog.show()
    application.processEvents()

    if action == "cancel":
        dialog.reject()
    else:
        dialog.close()
    application.processEvents()

    assert dialog.applied is False
    assert not output.exists()


def test_dialog_smoke_renders_nonblank_tree(
    application: QApplication, groups: RuleGroupInfo, tmp_path: Path
) -> None:
    dialog = RuleSelectDialog(groups, output_path=tmp_path / "unused.tsv")
    dialog.show()
    application.processEvents()
    image = dialog.grab().toImage()

    assert image.width() >= 480 and image.height() >= 400
    assert not image.isNull()
    assert dialog.tree.viewport().height() > 200
    assert dialog.apply_button.isEnabled() is True
    dialog.close()
    application.processEvents()
