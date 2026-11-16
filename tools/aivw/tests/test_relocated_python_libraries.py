"""The interpreter's active extension path owns runtime library identity."""

import sys
import sysconfig

from aivworkbench.runtime_info import _path_inside_root, _python_paths


def test_relocated_dynload_does_not_use_original_build_prefix(tmp_path, monkeypatch):
    prefix = tmp_path / "relocated Python"
    dynload = prefix / "lib/python3.9/lib-dynload"
    dynload.mkdir(parents=True)
    sysconfig.get_paths()  # Load interpreter configuration before isolating sys.path.
    monkeypatch.setattr(sys, "path", [str(dynload)])
    monkeypatch.setattr(sysconfig, "get_config_var", lambda name: "/private/build/lib-dynload")
    assert _python_paths()["libdynload"] == str(dynload)
    assert _path_inside_root(_python_paths()["libdynload"], str(prefix))


def test_external_or_ambiguous_dynload_is_not_accepted(tmp_path, monkeypatch):
    prefix = tmp_path / "python"
    external = tmp_path / "foreign/lib-dynload"
    external.mkdir(parents=True)
    monkeypatch.setattr(sys, "path", [str(external)])
    assert not _path_inside_root(_python_paths()["libdynload"], str(prefix))
    inside = prefix / "lib/python3.9/lib-dynload"
    inside.mkdir(parents=True)
    monkeypatch.setattr(sys, "path", [str(inside), str(external)])
    assert _python_paths()["libdynload"] is None


def test_missing_dynload_is_not_guessed_from_sysconfig(monkeypatch):
    monkeypatch.setattr(sys, "path", [])
    assert _python_paths()["libdynload"] is None
