"""Feature settings dialog uses the scoped command receipt and saves a group map."""

from concurrent.futures import Future
from types import SimpleNamespace

import pytest
from agent_gui_wait import wait_until
from PyQt5.QtWidgets import QApplication, QWidget
from sico.codex.feature_groups import describe
from sico.core.contracts import BoundContext
from sico_ui.feature_settings import (
    FeatureGroupList,
    FeatureSettingsDialog,
    checkbox_stylesheet,
)
from sico_ui.theme import DARK_PALETTE, LIGHT_PALETTE, active_palette


class Api:
    def __init__(self):
        self.saved = None
        self.project_saved = None
        self.context = BoundContext("instance", "generation", "target", {})
        self.feature_defaults = SimpleNamespace(
            read=self.project_read, save=self.project_save)

    def project_read(self):
        future = Future()
        future.set_result({"groups": describe(), "overrides": {}})
        return future

    def project_save(self, groups):
        future = Future()
        self.project_saved = groups
        future.set_result({"groups": describe(groups), "overrides": groups})
        return future

    def command(self, _handle, name, *args):
        future = Future()
        if name == "feature_settings":
            future.set_result({"groups": describe(), "overrides": {}, "bound": False})
        elif name == "configure_features":
            self.saved = args[0]
            future.set_result({
                "groups": describe(args[0]), "overrides": args[0], "bound": False})
        else:
            future.set_exception(ValueError("unexpected command"))
        return future

    def owns(self, _handle):
        return True

    def connection(self, _handle):
        return self.context.instance_id, self.context.generation


class Window(QWidget):
    def __init__(self):
        super().__init__()
        self.api = Api()
        self.page = SimpleNamespace(session="session", activation=0,
                                    reviewing=None, opening=False)
        self.presentation = SimpleNamespace(submission_context=self.api.context)
        self.lifecycle = SimpleNamespace(closing=False)
        self.info_app = SimpleNamespace(notice=lambda *_args: None)


@pytest.fixture
def app():
    application = QApplication.instance() or QApplication([])
    yield application
    application.processEvents()


def test_feature_dialog_reads_and_saves_a_group_map(app):
    window = Window()
    dialog = FeatureSettingsDialog(window)
    dialog.show()
    try:
        wait_until(dialog.ok.isEnabled)
        assert dialog.ok.isEnabled()
        check = dialog.editor.checks[("view_image", "features.view_image")]
        assert check.isChecked()
        check.setChecked(False)
        dialog.apply()
        wait_until(lambda: "已保存" in dialog.hint.text())
        assert window.api.saved == {"view_image": {"features.view_image": False}}
        assert not dialog.editor.checks[("view_image", "features.view_image")].isChecked()
    finally:
        dialog.close()
        window.close()


def test_feature_checkbox_outline_is_red_brown_in_both_themes(app):
    for palette in (LIGHT_PALETTE, DARK_PALETTE):
        style = checkbox_stylesheet(palette)
        assert "width: 15px; height: 15px;" in style
        assert f"border: 2px solid {palette.accent};" in style
        assert f"QCheckBox::indicator:checked {{ background-color: {palette.accent}; }}" in style
    editor = FeatureGroupList()
    assert editor.styleSheet() == checkbox_stylesheet(active_palette())


def test_project_feature_dialog_saves_the_project_default(app):
    from sico_ui.feature_settings import ProjectFeatureSettingsDialog

    window = Window()
    dialog = ProjectFeatureSettingsDialog(window)
    dialog.show()
    try:
        wait_until(dialog.ok.isEnabled)
        check = dialog.editor.checks[("view_image", "features.view_image")]
        check.setChecked(False)
        dialog.save()
        wait_until(lambda: "已保存" in dialog.hint.text())
        assert window.api.project_saved == {"view_image": {"features.view_image": False}}
    finally:
        dialog.close()
        window.close()
