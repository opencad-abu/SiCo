"""The terminal uses the shared Qt startup owner and explicit new settings."""

import importlib
import os
import sys
from pathlib import Path

import pytest

from sicoenv import read

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "tools/ai/terminal/pyqt"))
terminal = importlib.import_module("cad_ai_terminal.main")


@pytest.mark.parametrize("value", ["", "new-value"])
@pytest.mark.parametrize("suffix", ["PYTHON_LIBRARY_PATH", "QT_PLUGIN_PATH"])
def test_new_terminal_option_preserves_empty_and_overrides_legacy(value, suffix):
    env = {"SICO_AI_" + suffix: value, "CAD_AI_" + suffix: "old-value"}
    assert read(env, "SICO_AI_" + suffix) == value


def test_terminal_plugin_restore_uses_new_value(tmp_path, monkeypatch):
    for key in ("SICO_AI_QT_PLUGIN_PATH", "CAD_AI_QT_PLUGIN_PATH", "QT_PLUGIN_PATH"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SICO_AI_QT_PLUGIN_PATH", str(tmp_path))
    terminal._restore_qt_plugin_path()
    assert os.environ["QT_PLUGIN_PATH"] == str(tmp_path)
    monkeypatch.delenv("QT_PLUGIN_PATH")
    monkeypatch.setenv("SICO_AI_QT_PLUGIN_PATH", "")
    monkeypatch.setenv("CAD_AI_QT_PLUGIN_PATH", str(tmp_path))
    terminal._restore_qt_plugin_path()
    assert "QT_PLUGIN_PATH" not in os.environ


def test_terminal_reports_invalid_installation_without_starting_qt(monkeypatch, capsys):
    monkeypatch.setattr(terminal, "initialize", lambda: None)
    monkeypatch.setattr(terminal, "parse_launch_options", lambda _: object())
    monkeypatch.setattr(terminal, "_restore_qt_plugin_path", lambda: None)
    monkeypatch.setenv("SICO_HOME", "")
    monkeypatch.setenv("QT_QPA_PLATFORM", "xcb")
    assert terminal.main([]) == 2
    assert "SICO_HOME" in capsys.readouterr().err
