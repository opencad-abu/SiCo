"""The bundled Sarasa UI/Mono faces are loaded once and selected explicitly."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtGui import QFont, QFontInfo
from PyQt5.QtWidgets import QApplication

from cadgui import fonts


@pytest.fixture(autouse=True)
def source_installation(monkeypatch):
    monkeypatch.setenv("SICO_HOME", str(Path(__file__).resolve().parents[4]))
    monkeypatch.delenv("CAD_HOME", raising=False)


def test_install_loads_every_bundled_face_and_sets_the_ui_family():
    application = QApplication.instance() or QApplication([])
    loaded = fonts.install(application)

    assert loaded.missing_files == ()
    assert set(loaded.loaded_files) == set(fonts.FONT_FILES)
    assert loaded.ui_family == "Sarasa UI SC"
    assert loaded.mono_family == "Sarasa Mono SC"
    assert QFontInfo(application.font()).family() == "Sarasa UI SC"
    assert "Sarasa UI SC" in fonts.ui_css_family()
    assert "Sarasa Mono SC" in fonts.mono_css_family()


def test_semibold_and_terminal_faces_are_real_not_synthesized():
    application = QApplication.instance() or QApplication([])
    fonts.install(application)

    semibold = fonts.ui_font(weight=QFont.DemiBold)
    mono = fonts.mono_font()
    semibold_info = QFontInfo(semibold)
    assert semibold_info.styleName() == "SemiBold", (
        semibold_info.family(), semibold_info.styleName(), semibold_info.weight())
    assert QFontInfo(mono).family() == "Sarasa Mono SC"
    assert fonts.is_fixed_pitch(mono)
    terminal = fonts.terminal_font(point_size=16)
    assert terminal is not None
    assert terminal.pointSize() == 16
