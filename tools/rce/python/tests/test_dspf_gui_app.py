from __future__ import annotations

from pathlib import Path

import pytest
from sicopaths import IDENTITY_BYTES

import rcepy.dspf_gui.app as app_module
from rcepy.dspf_gui.app import check_xcb_runtime, prepare_qt_environment


def _installation(root):
    for name in ("bin", "tools/common", "etc/config"):
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / "etc/config/sico-install.json").write_bytes(IDENTITY_BYTES)
    return root


def test_qt_import_uses_pyqt5_and_qt5() -> None:
    QApplication, QColor, QPalette, qt_version = app_module._import_qt()

    assert QApplication.__module__.startswith("PyQt5.")
    assert QColor.__module__.startswith("PyQt5.")
    assert QPalette.__module__.startswith("PyQt5.")
    assert qt_version.startswith("5.")


def test_qt_environment_cleanup_preserves_platform_and_runtime() -> None:
    environment = {
        "QT_QPA_PLATFORM_PLUGIN_PATH": "/virtuoso/qt/plugins",
        "QT_PLUGIN_PATH": "/virtuoso/qt",
        "QT_DIR": "/virtuoso/qt",
        "QT_SELECT": "qt5",
        "QML2_IMPORT_PATH": "/virtuoso/qml",
        "QT_QPA_PLATFORM": "offscreen",
        "LD_LIBRARY_PATH": "/rce/python/lib",
    }
    removed = prepare_qt_environment(environment)
    assert set(removed) == {
        "QT_QPA_PLATFORM_PLUGIN_PATH",
        "QT_PLUGIN_PATH",
        "QT_DIR",
        "QT_SELECT",
        "QML2_IMPORT_PATH",
    }
    assert environment == {
        "QT_QPA_PLATFORM": "offscreen",
        "LD_LIBRARY_PATH": "/rce/python/lib",
    }


def test_xcb_prefers_cad_home_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundled = tmp_path / "cad/lib/libxcb-cursor.so.0"
    bundled.parent.mkdir(parents=True)
    bundled.touch()
    _installation(tmp_path / "cad")
    loaded = []
    handle = object()
    monkeypatch.setattr(
        app_module.ctypes, "CDLL", lambda name: (loaded.append(name), handle)[1]
    )

    check_xcb_runtime(
        {"CAD_HOME": str(tmp_path / "cad"), "QT_QPA_PLATFORM": "xcb"}
    )

    assert loaded == [str(bundled)]
    assert app_module._XCB_RUNTIME_HANDLE is handle


def test_xcb_falls_back_to_system_after_bundled_load_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundled = tmp_path / "cad/lib/libxcb-cursor.so.0"
    bundled.parent.mkdir(parents=True)
    bundled.touch()
    _installation(tmp_path / "cad")
    loaded = []

    system_handle = object()

    def load_library(name):
        loaded.append(name)
        if name == str(bundled):
            raise OSError("incompatible bundled library")
        return system_handle

    monkeypatch.setattr(app_module.ctypes, "CDLL", load_library)

    check_xcb_runtime(
        {"CAD_HOME": str(tmp_path / "cad"), "QT_QPA_PLATFORM": "xcb"}
    )

    assert loaded == [str(bundled), "libxcb-cursor.so.0"]
    assert app_module._XCB_RUNTIME_HANDLE is system_handle


def test_xcb_dependency_error_does_not_recommend_mixing_qt(monkeypatch) -> None:
    def missing_library(_name):
        raise OSError("missing")

    monkeypatch.setattr(app_module.ctypes, "CDLL", missing_library)
    with pytest.raises(RuntimeError, match="Do not add an Ansys Qt library"):
        check_xcb_runtime({"QT_QPA_PLATFORM": "xcb", "DISPLAY": ":1"})


def test_non_xcb_platform_does_not_probe_cursor_library(monkeypatch) -> None:
    monkeypatch.setattr(
        app_module.ctypes,
        "CDLL",
        lambda _name: pytest.fail("offscreen must not load an XCB dependency"),
    )
    check_xcb_runtime({"QT_QPA_PLATFORM": "offscreen"})
