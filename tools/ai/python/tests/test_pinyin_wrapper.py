"""Public command and per-GUI IME ownership; no private shell protocol remains."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from sico_pinyin import session

ROOT = Path(__file__).resolve().parents[4]
ENTRY = ROOT / "tools/ai/bin/sico-ai-pinyin"


@pytest.mark.parametrize("job", ["", "0", "-1", "abc", "１２"])
def test_local_input_environment_is_preserved(monkeypatch, tmp_path, job):
    monkeypatch.setenv("LSB_JOBID", job)
    expected = {"QT_IM_MODULE": "fcitx5", "XMODIFIERS": "@im=fcitx",
                "IBUS_ADDRESS_FILE": "/desktop/address"}
    for name, value in expected.items():
        monkeypatch.setenv(name, value)
    with session.input_session(tmp_path):
        assert {name: os.environ[name] for name in expected} == expected
    assert not (tmp_path / ".sico").exists()


def test_lsf_session_is_shared_by_nested_gui_calls_and_restored(monkeypatch, tmp_path):
    operations = []

    class Bus:
        def __init__(self, path):
            assert path == tmp_path
            operations.append("start")

        def ready(self):
            return "/private/ibus.address"

        def close(self):
            operations.append("close")

    monkeypatch.setattr(session, "PrivateBus", Bus)
    monkeypatch.setenv("LSB_JOBID", "123")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "desktop")
    monkeypatch.setenv("QT_IM_MODULE", "fcitx5")
    with session.input_session(tmp_path):
        assert os.environ["QT_IM_MODULE"] == "ibus"
        assert os.environ["IBUS_ADDRESS_FILE"] == "/private/ibus.address"
        assert "DBUS_SESSION_BUS_ADDRESS" not in os.environ
        with session.input_session(tmp_path):
            assert operations == ["start"]
    assert operations == ["start", "close"]
    assert os.environ["QT_IM_MODULE"] == "fcitx5"
    assert os.environ["DBUS_SESSION_BUS_ADDRESS"] == "desktop"


def test_missing_lsf_dependencies_warn_once_and_do_not_use_stale_desktop(monkeypatch, capsys):
    def unavailable(path):
        raise ValueError("IBus plugin missing")

    monkeypatch.setattr(session, "PrivateBus", unavailable)
    monkeypatch.setenv("LSB_JOBID", "123")
    monkeypatch.setenv("IBUS_ADDRESS", "stale-desktop")
    with session.input_session():
        with session.input_session():
            assert all(name not in os.environ for name in session.IME_NAMES)
    assert os.environ["IBUS_ADDRESS"] == "stale-desktop"
    assert capsys.readouterr().err.count("pinyin input is unavailable") == 1


def test_local_command_preserves_arguments_status_and_ime(tmp_path):
    env = dict(os.environ, SICO_HOME=str(ROOT), SICO_PYTHON=sys.executable,
               LSB_JOBID="0", QT_IM_MODULE="ibus", IBUS_ADDRESS="desktop")
    code = "import os,sys,json; print(json.dumps([sys.argv[1:],os.environ['IBUS_ADDRESS']])); sys.exit(17)"
    result = subprocess.run([str(ENTRY), "--", sys.executable, "-c", code, "中文 空格", "'"],
                            env=env, cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 17, result.stderr
    assert json.loads(result.stdout) == [["中文 空格", "'"], "desktop"]


def test_command_usage_and_removed_private_mode_fail_without_running(tmp_path):
    env = dict(os.environ, SICO_HOME=str(ROOT), SICO_PYTHON=sys.executable)
    for args in ([], ["--"], ["--cad-ai-private-pinyin-session", str(tmp_path), "--", "/bin/true"]):
        result = subprocess.run([str(ENTRY), *args], env=env, capture_output=True, text=True)
        assert result.returncode == 2
        assert "usage: sico-ai-pinyin -- COMMAND" in result.stderr
    assert not list(tmp_path.iterdir())
