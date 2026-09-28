from __future__ import annotations

from pathlib import Path

import pytest

from mtsnetlistor.gui.app import _logo_path


def test_logo_path_rejects_a_conflicting_installation(tmp_path):
    with pytest.raises(ValueError):
        _logo_path({"SICO_HOME": str(tmp_path)})


def test_logo_path_falls_back_to_repository_resource() -> None:
    expected = Path(__file__).resolve().parents[3] / "share/sico/icons/brand/logo.png"

    assert expected.is_file()
    assert _logo_path({}) == expected


def test_repository_logo_is_a_valid_qt_icon() -> None:
    qt_gui = pytest.importorskip("PyQt5.QtGui")
    logo = _logo_path({})

    assert logo is not None
    assert not qt_gui.QImage(str(logo)).isNull()


@pytest.mark.parametrize("configured", [False, True])
def test_gui_warns_for_missing_module_configuration_and_cleans_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured: bool,
) -> None:
    qt_widgets = pytest.importorskip("PyQt5.QtWidgets")
    from mtsnetlistor.gui import app

    application = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    monkeypatch.delenv("MTS_NETLISTOR_MODULEFILES", raising=False)
    if configured:
        monkeypatch.setenv("MTS_NETLISTOR_MODULEFILES", str(tmp_path / "missing"))
    monkeypatch.setattr(app, "prepare_qt_environment", lambda: None)
    monkeypatch.setattr(app, "check_xcb_runtime", lambda: None)
    monkeypatch.setattr(app, "_logo_path", lambda: None)

    messages = []

    def fake_notice(parent, title, text, **_kwargs):
        messages.append((parent, title, text))

    monkeypatch.setattr(app, "notice", fake_notice)

    session_path = tmp_path / "session.json"
    session_path.write_text("{}", encoding="utf-8")
    exits = []
    monkeypatch.setattr(app, "finalize_gui_exit", lambda status, owner: exits.append((status, owner)) or status)
    owner = (123, "owner-start")
    assert app.run_gui(session_path=session_path, parent_identity=owner) == 1
    assert messages and messages[0][1] == "MTS project module directory"
    assert "MTS_NETLISTOR_MODULEFILES" in messages[0][2]
    assert "--module-root" in messages[0][2]
    assert not session_path.exists()
    assert exits == [(1, owner)]
    assert application is qt_widgets.QApplication.instance()
