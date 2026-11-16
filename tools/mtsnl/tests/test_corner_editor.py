"""Corner editor state and profile editing without launching an EDA session."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt5.QtWidgets import QApplication

from mtsnetlistor.gui import corner_editor
from mtsnetlistor.gui.corner_editor import CornerEditor, ProfileDialog
from mtsnetlistor.model import CornerExport, CornerProfile, ModelEntry


def models(tmp_path):
    first = tmp_path / "first.scs"
    second = tmp_path / "second.scs"
    first.write_text("model first", encoding="utf-8")
    second.write_text("model second", encoding="utf-8")
    return (
        CornerProfile("tt", (
            ModelEntry(first, "tt", "typical", True),
            ModelEntry(second, "tt", "typical", True),
        )),
    )


@pytest.fixture(autouse=True)
def application():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    yield app


def test_editor_switches_fixed_and_library_state_and_emits_change(tmp_path):
    editor = CornerEditor()
    changes = []
    editor.changed.connect(lambda: changes.append(True))
    try:
        assert editor.value() == CornerExport("fixed", "mts_corner", ())
        assert editor.variable.isEnabled() is False
        assert editor.hint.text() == ""
        profiles = models(tmp_path)
        editor.set_value(CornerExport("library", "mts_corner", profiles))
        assert editor.value().mode == "library"
        assert editor.variable.isEnabled() is True
        assert "tt" in editor.hint.text()
        assert changes
    finally:
        editor.close()
        editor.deleteLater()


def test_editor_edit_profiles_accepts_dialog_result(monkeypatch, tmp_path):
    profiles = models(tmp_path)

    class Dialog:
        def __init__(self, seed, _parent):
            self.seed = seed

        def exec_(self):
            return corner_editor.QDialog.Accepted

        def profiles(self):
            return profiles

    monkeypatch.setattr(corner_editor, "ProfileDialog", Dialog)
    editor = CornerEditor()
    try:
        editor.edit_profiles()
        assert editor.value().profiles == profiles
    finally:
        editor.close()
        editor.deleteLater()


def test_profile_dialog_adds_renames_removes_and_moves_profiles(monkeypatch, tmp_path):
    profiles = models(tmp_path)
    dialog = ProfileDialog(profiles)
    try:
        monkeypatch.setattr(corner_editor, "ask_text",
                            lambda *_args, **_kwargs: ("ff", True))
        dialog.add_profile()
        assert [profile.name for profile in dialog.profiles()] == ["tt", "ff"]
        dialog.rename_profile()
        assert [profile.name for profile in dialog.profiles()] == ["tt", "ff"]
        dialog.remove_profile()
        assert [profile.name for profile in dialog.profiles()] == ["tt"]
    finally:
        dialog.close()
        dialog.deleteLater()


def test_profile_dialog_adds_models_and_moves_rows(monkeypatch, tmp_path):
    profiles = models(tmp_path)
    dialog = ProfileDialog(profiles)
    new_file = tmp_path / "new.scs"
    new_file.write_text("model new", encoding="utf-8")
    try:
        monkeypatch.setattr(corner_editor, "ask_files",
                            lambda *_args, **_kwargs: ([str(new_file)], None))
        dialog.add_model()
        assert len(dialog.profiles()[0].models) == 3
        dialog.table.selectRow(0)
        dialog.move_model(1)
        assert dialog.table.currentRow() == 1
        dialog.remove_model()
        assert len(dialog.profiles()[0].models) <= 3
    finally:
        dialog.close()
        dialog.deleteLater()
