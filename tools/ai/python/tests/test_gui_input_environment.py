"""Direct GUI launches prepare the same private scratch as AI Assistant."""

import os
from contextlib import contextmanager
from pathlib import Path

import pytest
from sico.service import gui_input


def test_gui_prepares_runtime_before_pinyin_and_restores_site_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("SICO_TEMP_DIR", raising=False)
    monkeypatch.delenv("CAD_TEMP_DIR", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("QT_IM_MODULE", "fcitx5")
    before = dict(os.environ)
    calls = []

    @contextmanager
    def input_session(project):
        assert project == tmp_path
        runtime = Path(os.environ["XDG_RUNTIME_DIR"])
        assert runtime == tmp_path / ".sico/ai/runtime"
        assert runtime.stat().st_mode & 0o777 == 0o700
        assert os.environ["SICO_TEMP_DIR"] == str(tmp_path / ".sico")
        assert os.environ["QT_IM_MODULE"] == "fcitx5"
        calls.append("start")
        try:
            yield
        finally:
            calls.append("close")

    monkeypatch.setattr(gui_input, "input_session", input_session)
    with pytest.raises(RuntimeError, match="GUI failed"):
        with gui_input.gui_input(tmp_path):
            raise RuntimeError("GUI failed")
    assert calls == ["start", "close"]
    assert dict(os.environ) == before


def test_invalid_state_is_not_silently_rebound(tmp_path, monkeypatch):
    monkeypatch.setenv("SICO_TEMP_DIR", ".//missing-user/.sico")
    monkeypatch.delenv("CAD_TEMP_DIR", raising=False)
    with pytest.raises(ValueError, match="project directory is unavailable"):
        with gui_input.gui_input(tmp_path):
            pytest.fail("invalid state must not start a desktop")
    assert os.environ["SICO_TEMP_DIR"] == ".//missing-user/.sico"
    assert not (tmp_path / ".sico").exists()
