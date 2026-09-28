"""Shared GUI dependency lookup obeys the marked SiCo installation boundary."""

from pathlib import Path

import pytest

from cadgui import environment
from sicopaths import IDENTITY_BYTES


def installation(root):
    for name in ("bin", "tools/common", "etc/config", "lib"):
        (root / name).mkdir(parents=True)
    (root / "etc/config/sico-install.json").write_bytes(IDENTITY_BYTES)
    (root / "lib/libxcb-cursor.so.0").touch()
    return root


def test_new_home_wins_and_site_alias_resolves(tmp_path, monkeypatch):
    root = installation(tmp_path / "new install")
    old = installation(tmp_path / "old install")
    alias = tmp_path / "site"
    alias.symlink_to(root, target_is_directory=True)
    loaded = []
    monkeypatch.setattr(environment.ctypes, "CDLL", lambda name: loaded.append(name))
    environment.check_xcb_runtime({"SICO_HOME": str(alias), "CAD_HOME": str(old),
                                       "QT_QPA_PLATFORM": "xcb"})
    assert loaded == [str(root / "lib/libxcb-cursor.so.0")]


@pytest.mark.parametrize("defect", ["empty", "unmarked", "escape"])
def test_invalid_new_installation_never_loads_another_library(tmp_path, monkeypatch, defect):
    root = installation(tmp_path / "install")
    if defect == "unmarked":
        (root / "etc/config/sico-install.json").unlink()
    if defect == "escape":
        library = root / "lib/libxcb-cursor.so.0"
        library.unlink()
        library.symlink_to(tmp_path / "foreign.so")
    monkeypatch.setattr(environment.ctypes, "CDLL", lambda _: pytest.fail("invalid root used"))
    with pytest.raises(ValueError):
        environment.check_xcb_runtime({"SICO_HOME": "" if defect == "empty" else str(root),
                                       "CAD_HOME": str(root), "QT_QPA_PLATFORM": "xcb"})
