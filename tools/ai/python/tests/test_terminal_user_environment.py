"""Terminal children receive user values without retaining the GUI's runtime state."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from cadenv import preserve_eda_temp_environment, preserve_virtuoso_environment
from sico.service.terminal_environment import shell_command, shell_environment
from sicosessionenv import ENVIRONMENT_NAMES


def user_environment():
    return {
        "PATH": "/site/bin:/usr/bin:/bin", "LD_LIBRARY_PATH": "/eda/lib",
        "PYTHONPATH": "/site/python", "MODULEPATH": "/site/modules",
        "LOADEDMODULES": "cadence/1:lsf/2", "_LMFILES_": "/site/modules/cadence",
        "MODULESHOME": "/site/module-system", "LMOD_CMD": "/site/lmod",
        "CDS_LIC_FILE": "5280@fixture", "LM_LICENSE_FILE": "27000@fixture",
        "HOME": "/home/fixture", "SHELL": "/bin/bash", "DISPLAY": ":99",
        "TMPDIR": "/site/tmp", "TEMP": "", "SITE_VALUE": "空 值\n=literal",
    }


def frontend_environment():
    source = user_environment()
    preserve_virtuoso_environment(source)
    preserve_eda_temp_environment(source)
    source.update(PATH="/sico/bin:/usr/bin:/bin", LD_LIBRARY_PATH="/sico/lib",
                  PYTHONPATH="/sico/python", MODULEPATH="/sico/modules",
                  TMPDIR="/work/.sico", TMP="/work/.sico", SICO_TEMP_DIR="/work/.sico",
                  SICO_API_KEY="synthetic-key", SICO_AI_CONTROL_FD="91",
                  CAD_AI_CONTROL_FD="92", PYTHONNOUSERSITE="1")
    return source


def test_user_values_are_restored_without_changing_the_frontend():
    source = frontend_environment()
    before = dict(source)
    result = shell_environment(source)
    assert result == user_environment()
    assert source == before
    result["PATH"] = "changed"
    assert source == before


def test_missing_and_empty_originals_survive_runtime_injection():
    source = {"PYTHONPATH": "", "MODULEPATH": ""}
    preserve_virtuoso_environment(source)
    source.update(LD_LIBRARY_PATH="/sico/lib", PATH="/sico/bin", PYTHONPATH="/sico/python")
    assert shell_environment(source) == {"PYTHONPATH": "", "MODULEPATH": ""}


def test_older_captures_keep_saved_empty_values_and_uncaptured_loaders_are_removed():
    assert shell_environment({"PATH": "/usr/bin", "LD_LIBRARY_PATH": "/sico/lib",
                              "SICO_VIRTUOSO_PYTHONPATH": ""}) == {
        "PATH": "/usr/bin", "PYTHONPATH": "",
    }


def test_runtime_credentials_and_control_names_are_not_shell_inputs():
    source = {name: "synthetic-secret" for name in ENVIRONMENT_NAMES}
    source.update(SICO_API_KEY="synthetic-secret", SICO_MCP_TOKEN="synthetic-secret",
                  SICO_AI_CONTROL_FD="17", CAD_AI_CONTROL_FD="18",
                  SICO_RESOURCE_TOKEN="synthetic-secret",
                  SICO_KNOWLEDGE_KEY="synthetic-secret", SITE_LICENSE="keep")
    assert shell_environment(source) == {"SITE_LICENSE": "keep"}
    command = shell_command(source)
    assert "synthetic-secret" not in " ".join(command)


@pytest.mark.parametrize("environment", [
    {"BAD=NAME": "value"}, {"": "value"}, {"BAD\0NAME": "value"},
    {"NAME": "bad\0value"}, {"NAME": 1}, {1: "value"},
    {"SICO_VIRTUOSO_PATH_SET": "1"},
    {"SICO_VIRTUOSO_PATH": "/bin", "SICO_VIRTUOSO_PATH_SET": "0"},
    {"SICO_VIRTUOSO_PATH": "", "SICO_VIRTUOSO_PATH_SET": "unknown"},
    {"CAD_VIRTUOSO_PATH": "/old"},
])
def test_invalid_environment_is_rejected_before_launch(environment):
    with pytest.raises(ValueError):
        shell_command(environment)


@pytest.mark.parametrize("command", [[], "pwd", [""], ["/bin/sh", None], ["bad\0arg"]])
def test_invalid_explicit_commands_are_rejected(command):
    with pytest.raises(ValueError, match="command"):
        shell_command({}, command=command)


def test_relative_shell_is_rejected():
    with pytest.raises(ValueError, match="absolute"):
        shell_command({"SHELL": "bash"})


def test_real_child_after_parent_merge_receives_restored_values():
    source = frontend_environment()
    source["PYTHONHOME"] = "/sico/python"
    child = shell_environment(source)
    # QTermWidget merges its explicit environment with its parent. Deletion
    # must happen in the env wrapper, rather than merely in the supplied dict.
    merged = dict(source, **child)
    names = sorted(set(user_environment()) | {"TMP", "SICO_API_KEY", "SICO_AI_CONTROL_FD",
                   "CAD_AI_CONTROL_FD", "PYTHONHOME", "SICO_VIRTUOSO_PATH"})
    script = "import json,os;print(json.dumps({k:os.environ[k] for k in " + repr(names) + " if k in os.environ}))"
    argv = shell_command(source, command=["/bin/sh", "-c", 'exec "$@"', "terminal-test",
                                          sys.executable, "-B", "-S", "-c", script])
    assert all(value not in argv for value in user_environment().values() if value)
    completed = subprocess.run(argv, env=merged, capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == user_environment()


def test_real_launcher_captures_before_sanitizing_and_preserves_recapture(tmp_path):
    source_root = Path(__file__).resolve().parents[4]
    root = tmp_path / "sico"
    for relative in ("bin", "tools/common/sh", "tools/sico/bin", "tools/sico/python",
                     "etc/config", "runtime/python/bin"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_root / "bin/sico", root / "bin/sico")
    shutil.copy2(source_root / "tools/sico/bin/sico", root / "tools/sico/bin/sico")
    shutil.copy2(
        source_root / "tools/common/sh/sico-installation.sh",
        root / "tools/common/sh/sico-installation.sh",
    )
    shutil.copy2(
        source_root / "etc/config/sico-install.json",
        root / "etc/config/sico-install.json",
    )
    (root / "tools/sico/python/sico-entry").write_text("", encoding="ascii")
    capture = tmp_path / "capture.json"
    interpreter = root / "runtime/python/bin/python3"
    interpreter.write_text("#!" + sys.executable + "\nimport json,os\n"
                           'open(os.environ["CAPTURE"],"w").write(json.dumps(dict(os.environ)))\n')
    interpreter.chmod(0o700)
    source = dict(os.environ)
    for name in tuple(source):
        if name.startswith(("SICO_VIRTUOSO_", "CAD_VIRTUOSO_", "CAD_ORIG_", "SICO_ORIG_")):
            del source[name]
    source.update(SICO_HOME=str(root), SICO_PYTHON=str(interpreter), CAPTURE=str(capture),
                  LD_LIBRARY_PATH="/eda/lib", PYTHONPATH="/site/python", MODULEPATH="",
                  SICO_SYSTEM_LD_LIBRARY_PATH="/sico/lib")
    for name in ("LOADEDMODULES", "LD_PRELOAD", "LD_AUDIT", "PYTHONHOME",
                 "SICO_AI_PYTHON_LIBRARY_PATH", "SICO_CODEX_PYTHON_LIBRARY_PATH"):
        source.pop(name, None)
    subprocess.run([str(root / "bin/sico")], env=source, check=True, timeout=5,
                   cwd=tmp_path)
    captured = json.loads(capture.read_text())
    assert captured["LD_LIBRARY_PATH"] == f"{root}/runtime/python/lib:{root}/lib"
    assert "PYTHONPATH" not in captured
    assert captured["SICO_VIRTUOSO_MODULEPATH_SET"] == "1"
    assert captured["SICO_VIRTUOSO_LOADEDMODULES_SET"] == "0"
    restored = shell_environment(captured)
    assert restored["LD_LIBRARY_PATH"] == "/eda/lib"
    assert restored["PYTHONPATH"] == "/site/python"
    assert restored["MODULEPATH"] == "" and "LOADEDMODULES" not in restored
    subprocess.run([str(root / "bin/sico")], env=captured, check=True, timeout=5,
                   cwd=tmp_path)
    recaptured = json.loads(capture.read_text())
    for name, value in captured.items():
        if name.startswith("SICO_VIRTUOSO_"):
            assert recaptured[name] == value


def test_selected_host_replaces_desktop_values_and_set_markers(monkeypatch):
    from threading import Event, Lock
    from types import SimpleNamespace

    from sico.service import platform_connection

    selected = {"PATH": "/selected/bin", "MODULEPATH": ""}
    received = []

    class Bridge:
        descriptor = {}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def platform_environment(self, **kwargs):
            return dict(selected)

    def opened(*args, **kwargs):
        received.append(kwargs["environment"])
        return "controller"

    monkeypatch.setattr(platform_connection, "connect_candidate", lambda *args, **kwargs: (Bridge(), {}))
    attach = SimpleNamespace(
        _dependencies=lambda _: None, _new=lambda _: None, _existing_target=lambda _: None,
        owner=SimpleNamespace(project=None, _mutation=Lock(), open=opened),
    )
    desktop = {"PATH": "/desktop/bin", "LD_LIBRARY_PATH": "/desktop/lib"}
    preserve_virtuoso_environment(desktop)
    desktop["SICO_API_KEY"] = "synthetic-secret"
    original = dict(desktop)
    controller, created = platform_connection.open_platform(attach, SimpleNamespace(closed=Event()), {
        "session_id": "session", "platform": "CDNS-IC", "candidate": "candidate",
        "provider_config": None, "environment": desktop,
    })
    assert (controller, created) == ("controller", True)
    assert desktop == original
    assert received[0]["SICO_VIRTUOSO_LD_LIBRARY_PATH_SET"] == "0"
    assert received[0]["SICO_VIRTUOSO_MODULEPATH_SET"] == "1"
    assert received[0]["SICO_API_KEY"] == "synthetic-secret"
    assert shell_environment(received[0]) == selected


def test_python_entry_captures_before_switching_interpreters(monkeypatch):
    from sico import entry, interpreter

    captured = []

    class StopBeforeRuntime(Exception):
        pass

    def ensure(_):
        captured.append(dict(os.environ))
        raise StopBeforeRuntime()

    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(interpreter, "ensure_cad_python", ensure)
    source = dict(os.environ)
    for name in tuple(source):
        if name.startswith(("SICO_VIRTUOSO_", "CAD_VIRTUOSO_")):
            del source[name]
    source.update(LD_LIBRARY_PATH="/user/lib", PYTHONPATH="")
    source.pop("MODULEPATH", None)
    monkeypatch.setattr(os, "environ", source)
    with pytest.raises(StopBeforeRuntime):
        entry.main([])
    assert captured[0]["SICO_VIRTUOSO_LD_LIBRARY_PATH"] == "/user/lib"
    assert captured[0]["SICO_VIRTUOSO_PYTHONPATH_SET"] == "1"
    assert captured[0]["SICO_VIRTUOSO_MODULEPATH_SET"] == "0"


def test_explicit_session_environment_removes_values_inherited_only_from_gui():
    source = {"PATH": "/usr/bin:/bin", "SITE_VALUE": "session=value"}
    inherited = {"PATH": "/gui/bin", "SICO_AI_TOKEN": "synthetic-secret",
                 "PYTHONHOME": "/gui/python", "GUI_ONLY": "not-selected"}
    child = shell_environment(source)
    script = 'import json,os;print(json.dumps({k:os.environ[k] for k in '
    script += repr(["PATH", "SITE_VALUE", "SICO_AI_TOKEN", "GUI_ONLY", "PYTHONHOME"])
    script += ' if k in os.environ}))'
    command = shell_command(source, inherited=inherited,
                            command=[sys.executable, "-B", "-S", "-c", script])
    assert "synthetic-secret" not in command
    completed = subprocess.run(command, env=dict(inherited, **child),
                               capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == source
